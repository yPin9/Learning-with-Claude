# Ch 26 — Active Directory 模組

> 目標：用 `ActiveDirectory` 模組管理 AD 使用者、群組、OU，掌握 AD Filter 語法，能批次操作帳號。

## 安裝 AD 模組

AD 模組是 RSAT（Remote Server Administration Tools）的一部分：

```powershell
# Windows 10/11（功能選項方式安裝）
Add-WindowsCapability -Online -Name "Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0"

# 或透過 PowerShell（需要管理員）
Install-WindowsFeature -Name RSAT-AD-PowerShell   # Server 上用這個

# 確認模組已載入
Get-Module -ListAvailable -Name ActiveDirectory
Import-Module ActiveDirectory   # 通常自動載入，手動加以防萬一
```

## 查詢使用者

```powershell
# 取得單一使用者
Get-ADUser -Identity "jdoe"
Get-ADUser -Identity "jdoe" -Properties *   # 所有屬性（預設只傳回部分）

# 搜尋
Get-ADUser -Filter { Name -like "John*" }
Get-ADUser -Filter { Department -eq "IT" -and Enabled -eq $true }

# 用 LDAPFilter
Get-ADUser -LDAPFilter "(department=IT)"

# 指定 OU 搜尋（不遞迴）
Get-ADUser -Filter * -SearchBase "OU=IT,DC=corp,DC=com"

# 遞迴搜尋 OU 和子 OU
Get-ADUser -Filter * -SearchBase "OU=Users,DC=corp,DC=com" -SearchScope Subtree

# 常用屬性
Get-ADUser -Identity "jdoe" -Properties EmailAddress, Department, LastLogonDate, PasswordLastSet, LockedOut
```

## AD Filter 語法

AD Filter 和 PowerShell 比較運算子類似，但有差異：

```powershell
# 基本比較
-Filter { Name -eq "John Doe" }
-Filter { Enabled -eq $true }
-Filter { Department -like "IT*" }

# 邏輯運算
-Filter { Department -eq "IT" -and Enabled -eq $true }
-Filter { Title -eq "Manager" -or Title -eq "Director" }
-Filter { Name -ne "Guest" }

# 萬用字元（只支援 -like）
-Filter { Name -like "John*" }

# 注意：不能用 -gt -lt 在大部分 AD 屬性上，要用 LDAPFilter 或後處理
```

## 建立和修改使用者

```powershell
# 建立使用者
New-ADUser `
    -Name "Jane Smith" `
    -SamAccountName "jsmith" `
    -UserPrincipalName "jsmith@corp.com" `
    -GivenName "Jane" `
    -Surname "Smith" `
    -DisplayName "Jane Smith" `
    -EmailAddress "jsmith@corp.com" `
    -Department "Marketing" `
    -Title "Marketing Specialist" `
    -Path "OU=Marketing,DC=corp,DC=com" `
    -AccountPassword (ConvertTo-SecureString "Init@12345" -AsPlainText -Force) `
    -ChangePasswordAtLogon $true `
    -Enabled $true

# 修改屬性
Set-ADUser -Identity "jdoe" `
    -Title "Senior Engineer" `
    -Department "IT" `
    -EmailAddress "john.doe@corp.com"

# 啟用 / 停用
Enable-ADAccount  -Identity "jdoe"
Disable-ADAccount -Identity "jdoe"

# 解鎖帳號
Unlock-ADAccount -Identity "jdoe"

# 重設密碼（下次登入需改密碼）
Set-ADAccountPassword -Identity "jdoe" `
    -NewPassword (ConvertTo-SecureString "NewP@ss123!" -AsPlainText -Force) `
    -Reset
Set-ADUser -Identity "jdoe" -ChangePasswordAtLogon $true
```

## 刪除使用者

```powershell
# 刪除（不可恢復，謹慎）
Remove-ADUser -Identity "jdoe" -Confirm:$false

# 更好的做法：先停用，等一段時間再刪
Disable-ADAccount -Identity "jdoe"
Set-ADUser -Identity "jdoe" -Description "DISABLED $(Get-Date -Format 'yyyy-MM-dd')"
Move-ADObject -Identity (Get-ADUser "jdoe").DistinguishedName `
    -TargetPath "OU=Disabled,DC=corp,DC=com"
```

## 群組管理

```powershell
# 查詢群組
Get-ADGroup -Identity "IT-Admins"
Get-ADGroup -Filter { Name -like "IT-*" }

# 查群組成員
Get-ADGroupMember -Identity "IT-Admins"
Get-ADGroupMember -Identity "IT-Admins" -Recursive   # 遞迴（含巢狀群組）

# 查使用者加入哪些群組
Get-ADUser -Identity "jdoe" -Properties MemberOf | Select-Object -ExpandProperty MemberOf

# 加入群組
Add-ADGroupMember -Identity "IT-Admins" -Members "jdoe"
Add-ADGroupMember -Identity "IT-Admins" -Members @("user1","user2","user3")

# 從群組移除
Remove-ADGroupMember -Identity "IT-Admins" -Members "jdoe" -Confirm:$false

# 建立群組
New-ADGroup `
    -Name "App-PowerUsers" `
    -GroupScope Global `
    -GroupCategory Security `
    -Path "OU=Groups,DC=corp,DC=com" `
    -Description "應用程式高級使用者"
```

## OU 和電腦管理

```powershell
# 查詢 OU
Get-ADOrganizationalUnit -Filter { Name -like "IT*" }
Get-ADOrganizationalUnit -Filter *

# 查詢電腦
Get-ADComputer -Filter { OperatingSystem -like "Windows Server*" }
Get-ADComputer -Identity "server01" -Properties *

# 查詢超過 90 天未登入的電腦
$cutoff = (Get-Date).AddDays(-90)
Get-ADComputer -Filter { Enabled -eq $true } -Properties LastLogonDate |
    Where-Object { $_.LastLogonDate -lt $cutoff -or -not $_.LastLogonDate } |
    Select-Object Name, LastLogonDate |
    Sort-Object LastLogonDate
```

## 批次建立使用者範例

```powershell
# users.csv：
# SamAccountName,GivenName,Surname,Department,Title,OU
# jdoe,John,Doe,IT,Engineer,"OU=IT,DC=corp,DC=com"

$users = Import-Csv C:\Data\new-users.csv -Encoding utf8
$defaultPass = ConvertTo-SecureString "Welcome@2024" -AsPlainText -Force

foreach ($u in $users) {
    if (Get-ADUser -Filter { SamAccountName -eq $u.SamAccountName } -ErrorAction SilentlyContinue) {
        Write-Warning "$($u.SamAccountName) 已存在，跳過"
        continue
    }

    New-ADUser `
        -SamAccountName $u.SamAccountName `
        -Name "$($u.GivenName) $($u.Surname)" `
        -GivenName $u.GivenName `
        -Surname $u.Surname `
        -UserPrincipalName "$($u.SamAccountName)@corp.com" `
        -Department $u.Department `
        -Title $u.Title `
        -Path $u.OU `
        -AccountPassword $defaultPass `
        -ChangePasswordAtLogon $true `
        -Enabled $true

    Write-Host "建立：$($u.SamAccountName)" -ForegroundColor Green
}
```

## 動手練習

```powershell
# 如果有 AD 環境：
# 1. 找出過去 30 天內新建立的使用者
$cutoff = (Get-Date).AddDays(-30)
Get-ADUser -Filter { whenCreated -ge $cutoff } -Properties whenCreated |
    Select-Object Name, SamAccountName, whenCreated |
    Sort-Object whenCreated -Descending

# 2. 找出密碼超過 90 天未換的啟用帳號
$passCutoff = (Get-Date).AddDays(-90)
Get-ADUser -Filter { Enabled -eq $true -and PasswordNeverExpires -eq $false } `
    -Properties PasswordLastSet |
    Where-Object { $_.PasswordLastSet -lt $passCutoff } |
    Select-Object Name, SamAccountName, PasswordLastSet |
    Sort-Object PasswordLastSet
```

## 自我檢核

- [ ] 知道 `-Properties *` 才能看到全部屬性（預設只傳部分）
- [ ] 理解 AD Filter 語法：`-Filter { Property -eq Value }`
- [ ] 知道刪除帳號的最佳實踐是「先停用→移到 Disabled OU→一段時間後再刪」
- [ ] 能批次從 CSV 建立 AD 使用者

→ [Ch 27 GPO、DNS、DHCP 腳本化](./27-gpo-dns-dhcp.md)
