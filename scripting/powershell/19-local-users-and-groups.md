# Ch 19 — 本機使用者與群組

> 目標：用 PowerShell 管理 Windows 本機帳號和群組，取代 `net user` / `lusrmgr.msc`。

## LocalAccounts 模組

所有本機帳號管理的 cmdlet 都在 `Microsoft.PowerShell.LocalAccounts` 模組，Windows 10/11 和 Server 2016+ 已內建：

```powershell
Get-Module -ListAvailable -Name Microsoft.PowerShell.LocalAccounts
Get-Command -Module Microsoft.PowerShell.LocalAccounts
```

## 查詢使用者

```powershell
# 列出所有本機使用者
Get-LocalUser

# 查特定帳號
Get-LocalUser -Name "Administrator"

# 查詳細資訊（包含最後登入時間、密碼到期）
Get-LocalUser | Format-List *

# 只看啟用的帳號
Get-LocalUser | Where-Object { $_.Enabled }
```

常用屬性：

| 屬性 | 說明 |
|------|------|
| `Name` | 帳號名稱 |
| `Enabled` | 是否啟用 |
| `PasswordExpires` | 密碼到期時間（$null = 永不到期）|
| `LastLogon` | 最後登入時間 |
| `PasswordLastSet` | 密碼最後修改時間 |

## 建立使用者

```powershell
# 建立帶密碼的帳號
$password = ConvertTo-SecureString "P@ssw0rd123!" -AsPlainText -Force
New-LocalUser -Name "jdoe" `
    -Password $password `
    -FullName "John Doe" `
    -Description "業務部門帳號" `
    -PasswordNeverExpires $false `
    -UserMayNotChangePassword $false

# 建立不需密碼的帳號（服務帳號）
New-LocalUser -Name "svc_backup" `
    -NoPassword `
    -Description "備份服務帳號"

# 互動式輸入密碼（腳本裡避免明文）
$password = Read-Host "輸入密碼" -AsSecureString
New-LocalUser -Name "newuser" -Password $password
```

**不要在腳本裡硬寫明文密碼**。`ConvertTo-SecureString -AsPlainText -Force` 只在測試或知道密碼來源安全時使用。

## 修改使用者

```powershell
# 啟用 / 停用
Enable-LocalUser  -Name "jdoe"
Disable-LocalUser -Name "jdoe"

# 修改屬性
Set-LocalUser -Name "jdoe" `
    -FullName "John M. Doe" `
    -Description "業務部門高級帳號"

# 修改密碼
$newPass = ConvertTo-SecureString "NewP@ss456!" -AsPlainText -Force
Set-LocalUser -Name "jdoe" -Password $newPass

# 設定密碼永不到期
Set-LocalUser -Name "svc_backup" -PasswordNeverExpires $true
```

## 刪除使用者

```powershell
Remove-LocalUser -Name "jdoe"
Remove-LocalUser -Name "jdoe" -Confirm:$false   # 不問確認
```

## 查詢群組

```powershell
# 列出所有本機群組
Get-LocalGroup

# 查特定群組的成員
Get-LocalGroupMember -Group "Administrators"
Get-LocalGroupMember -Group "Remote Desktop Users"

# 查使用者在哪些群組
Get-LocalGroup | ForEach-Object {
    $group = $_.Name
    Get-LocalGroupMember -Group $group -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*jdoe*" } |
        ForEach-Object { "jdoe 在群組：$group" }
}
```

## 群組成員管理

```powershell
# 加入群組
Add-LocalGroupMember -Group "Remote Desktop Users" -Member "jdoe"
Add-LocalGroupMember -Group "Administrators" -Member "jdoe"

# 從群組移除
Remove-LocalGroupMember -Group "Remote Desktop Users" -Member "jdoe"

# 建立新群組
New-LocalGroup -Name "AppUsers" -Description "應用程式使用者群組"

# 刪除群組
Remove-LocalGroup -Name "AppUsers"
```

## 批次建立帳號

```powershell
# 從 CSV 批次建立帳號
# users.csv 格式：Name,FullName,Department,Password
$users = Import-Csv C:\Data\new-users.csv -Encoding utf8

foreach ($u in $users) {
    if (Get-LocalUser -Name $u.Name -ErrorAction SilentlyContinue) {
        Write-Warning "$($u.Name) 已存在，跳過"
        continue
    }

    $pass = ConvertTo-SecureString $u.Password -AsPlainText -Force
    New-LocalUser -Name $u.Name `
        -Password $pass `
        -FullName $u.FullName `
        -Description $u.Department `
        -PasswordNeverExpires $false

    # 加入對應的群組
    Add-LocalGroupMember -Group "Users" -Member $u.Name

    Write-Host "已建立帳號：$($u.Name)" -ForegroundColor Green
}
```

## 帳號安全稽核

```powershell
# 找出超過 90 天沒登入的帳號
$cutoff = (Get-Date).AddDays(-90)
Get-LocalUser |
    Where-Object {
        $_.Enabled -and
        $_.LastLogon -ne $null -and
        $_.LastLogon -lt $cutoff
    } |
    Select-Object Name, LastLogon, PasswordLastSet |
    Format-Table

# 找出密碼超過 180 天沒換的啟用帳號
$passAge = (Get-Date).AddDays(-180)
Get-LocalUser |
    Where-Object {
        $_.Enabled -and
        $_.PasswordLastSet -ne $null -and
        $_.PasswordLastSet -lt $passAge -and
        -not $_.PasswordNeverExpires
    } |
    Select-Object Name, PasswordLastSet
```

## 動手練習

```powershell
# 建立三個測試帳號，加入群組，然後清理
$testUsers = @("test_alice", "test_bob", "test_carol")
$pass = ConvertTo-SecureString "Test@1234" -AsPlainText -Force

foreach ($name in $testUsers) {
    New-LocalUser -Name $name -Password $pass -Description "測試帳號"
    Add-LocalGroupMember -Group "Users" -Member $name
    Write-Host "建立：$name"
}

# 確認
Get-LocalUser | Where-Object { $_.Name -like "test_*" } | Format-Table Name, Enabled

# 清理
$testUsers | Remove-LocalUser
Write-Host "清理完成"
```

## 自我檢核

- [ ] 知道不要在腳本裡用 `-AsPlainText -Force` 放明文密碼（測試除外）
- [ ] 能批次從 CSV 建立使用者
- [ ] 能找出長時間未登入或密碼過久的帳號
- [ ] 理解 `Add-LocalGroupMember` 和 `Remove-LocalGroupMember` 的使用方式

→ [Ch 20 網路管理工具](./20-network-tools.md)
