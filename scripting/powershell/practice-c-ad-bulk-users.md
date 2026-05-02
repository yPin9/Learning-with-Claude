# 練習 C — AD 批次建立使用者腳本

> 目標：整合 Part 5（Ch 24–27）的知識，寫一個從 CSV 批次建立 AD 使用者、加入對應群組、並產出結果報告的腳本。

## 任務規格

| 項目 | 說明 |
|------|------|
| 輸入 | CSV 檔：姓名、帳號、部門、職稱、群組、OU |
| 處理 | 建立 AD 帳號 → 設定初始密碼 → 加入群組 → 記錄結果 |
| 輸出 | Console 進度 + 結果 CSV（成功/失敗清單）|
| 模式 | 支援 `-WhatIf`（預覽不執行）|
| 錯誤處理 | 單一帳號失敗不中斷整批，記錄到結果 CSV |
| 重複帳號 | 偵測已存在的帳號，跳過並標記 |

## 期望輸出

```
=== AD 批次建立使用者 ===
來源：C:\Data\new-users.csv（共 10 筆）

[1/10] 建立：jdoe (John Doe) IT → 成功
[2/10] 建立：mchen (Mary Chen) HR → 成功
[3/10] 建立：bwang (Bob Wang) IT → 跳過（已存在）
[4/10] 建立：alee (Alice Lee) Finance → 成功
...

=== 結果摘要 ===
成功：7  跳過：2  失敗：1

結果報告：C:\Temp\ad-bulk-result.csv
```

## CSV 格式

`new-users.csv`：

```csv
SamAccountName,GivenName,Surname,Department,Title,Groups,OU
jdoe,John,Doe,IT,Engineer,"IT-Staff;IT-VPN","OU=IT,DC=corp,DC=com"
mchen,Mary,Chen,HR,Specialist,HR-Staff,"OU=HR,DC=corp,DC=com"
alee,Alice,Lee,Finance,Analyst,"Finance-Staff;Finance-Reports","OU=Finance,DC=corp,DC=com"
```

`Groups` 欄位用分號分隔多個群組。

## 實作步驟建議

### Step 1：腳本框架

```powershell
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path $_ -PathType Leaf })]
    [string]$CsvPath,

    [string]$ResultPath = "C:\Temp\ad-bulk-result.csv",

    [ValidatePattern("^(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{8,}$")]
    [string]$DefaultPassword = "Welcome@2024"
)
```

### Step 2：處理每筆使用者

```powershell
function New-ADUserFromRecord {
    [CmdletBinding(SupportsShouldProcess)]
    param(
        $Record,
        [string]$Password
    )

    # 檢查是否已存在
    if (Get-ADUser -Filter { SamAccountName -eq $Record.SamAccountName } -ErrorAction SilentlyContinue) {
        return [PSCustomObject]@{
            SamAccountName = $Record.SamAccountName
            Name           = "$($Record.GivenName) $($Record.Surname)"
            Status         = "SKIP"
            Message        = "帳號已存在"
        }
    }

    # 建立帳號
    if ($PSCmdlet.ShouldProcess($Record.SamAccountName, "建立 AD 使用者")) {
        try {
            $secPass = ConvertTo-SecureString $Password -AsPlainText -Force

            New-ADUser `
                -SamAccountName $Record.SamAccountName `
                -Name "$($Record.GivenName) $($Record.Surname)" `
                -GivenName $Record.GivenName `
                -Surname $Record.Surname `
                -UserPrincipalName "$($Record.SamAccountName)@corp.com" `
                -Department $Record.Department `
                -Title $Record.Title `
                -Path $Record.OU `
                -AccountPassword $secPass `
                -ChangePasswordAtLogon $true `
                -Enabled $true `
                -ErrorAction Stop

            # 加入群組
            if ($Record.Groups) {
                $groups = $Record.Groups -split ";"
                foreach ($group in $groups) {
                    $group = $group.Trim()
                    if ($group) {
                        Add-ADGroupMember -Identity $group -Members $Record.SamAccountName -ErrorAction Continue
                    }
                }
            }

            return [PSCustomObject]@{
                SamAccountName = $Record.SamAccountName
                Name           = "$($Record.GivenName) $($Record.Surname)"
                Status         = "OK"
                Message        = "建立成功"
            }

        } catch {
            return [PSCustomObject]@{
                SamAccountName = $Record.SamAccountName
                Name           = "$($Record.GivenName) $($Record.Surname)"
                Status         = "FAIL"
                Message        = $_.Exception.Message
            }
        }
    }
}
```

### Step 3：主流程 + 統計

```powershell
$users  = Import-Csv $CsvPath -Encoding utf8
$total  = $users.Count
$ok = $skip = $fail = 0
$results = [System.Collections.Generic.List[object]]::new()

for ($i = 0; $i -lt $total; $i++) {
    $u = $users[$i]
    Write-Host "[$($i+1)/$total] 處理：$($u.SamAccountName)..." -NoNewline

    $result = New-ADUserFromRecord -Record $u -Password $DefaultPassword
    $results.Add($result)

    switch ($result.Status) {
        "OK"   { $ok++;   Write-Host " 成功" -ForegroundColor Green }
        "SKIP" { $skip++; Write-Host " 跳過（已存在）" -ForegroundColor Yellow }
        "FAIL" { $fail++; Write-Host " 失敗：$($result.Message)" -ForegroundColor Red }
    }
}
```

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作</summary>

```powershell
# Invoke-ADUserProvisioning.ps1
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path $_ -PathType Leaf })]
    [string]$CsvPath,

    [string]$ResultPath = "C:\Temp\ad-bulk-result.csv",
    [string]$DefaultPassword = "Welcome@2024"
)

$ErrorActionPreference = "Continue"

function Add-SingleUser {
    [CmdletBinding(SupportsShouldProcess)]
    param($User, [string]$Pass)

    $fullName = "$($User.GivenName) $($User.Surname)"

    if (Get-ADUser -Filter { SamAccountName -eq $User.SamAccountName } -ErrorAction SilentlyContinue) {
        return [PSCustomObject]@{ SamAccountName=$User.SamAccountName; Name=$fullName; Status="SKIP"; Message="已存在" }
    }

    if (-not $PSCmdlet.ShouldProcess($User.SamAccountName, "New-ADUser")) {
        return [PSCustomObject]@{ SamAccountName=$User.SamAccountName; Name=$fullName; Status="WHATIF"; Message="WhatIf" }
    }

    try {
        $secPass = ConvertTo-SecureString $Pass -AsPlainText -Force
        New-ADUser -SamAccountName $User.SamAccountName -Name $fullName `
            -GivenName $User.GivenName -Surname $User.Surname `
            -UserPrincipalName "$($User.SamAccountName)@corp.com" `
            -Department $User.Department -Title $User.Title `
            -Path $User.OU -AccountPassword $secPass `
            -ChangePasswordAtLogon $true -Enabled $true -ErrorAction Stop

        if ($User.Groups) {
            $User.Groups -split ";" | ForEach-Object {
                $g = $_.Trim()
                if ($g) { Add-ADGroupMember -Identity $g -Members $User.SamAccountName -ErrorAction SilentlyContinue }
            }
        }
        return [PSCustomObject]@{ SamAccountName=$User.SamAccountName; Name=$fullName; Status="OK"; Message="成功" }
    } catch {
        return [PSCustomObject]@{ SamAccountName=$User.SamAccountName; Name=$fullName; Status="FAIL"; Message=$_.Exception.Message }
    }
}

Write-Host "`n=== AD 批次建立使用者 ===" -ForegroundColor Cyan
$users = Import-Csv $CsvPath -Encoding utf8
Write-Host "來源：$CsvPath（共 $($users.Count) 筆）`n"

$ok=$skip=$fail=0
$results = [System.Collections.Generic.List[object]]::new()

for ($i = 0; $i -lt $users.Count; $i++) {
    $u = $users[$i]
    Write-Host "[$($i+1)/$($users.Count)] $($u.SamAccountName) ($($u.GivenName) $($u.Surname))" -NoNewline

    $r = Add-SingleUser -User $u -Pass $DefaultPassword
    $results.Add($r)

    switch ($r.Status) {
        "OK"     { $ok++;   Write-Host " → 成功" -ForegroundColor Green  }
        "SKIP"   { $skip++; Write-Host " → 跳過" -ForegroundColor Yellow }
        "WHATIF" { Write-Host " → [WhatIf]" -ForegroundColor Cyan  }
        "FAIL"   { $fail++; Write-Host " → 失敗：$($r.Message)" -ForegroundColor Red }
    }
}

Write-Host "`n=== 結果摘要 ===" -ForegroundColor Cyan
Write-Host "成功：$ok  跳過：$skip  失敗：$fail"

New-Item (Split-Path $ResultPath -Parent) -ItemType Directory -Force | Out-Null
$results | Export-Csv $ResultPath -NoTypeInformation -Encoding utf8
Write-Host "結果報告：$ResultPath"
```

</details>

## 測試用例

```powershell
# 1. WhatIf 預覽
.\Invoke-ADUserProvisioning.ps1 -CsvPath C:\Data\new-users.csv -WhatIf

# 2. 實際執行
.\Invoke-ADUserProvisioning.ps1 -CsvPath C:\Data\new-users.csv

# 3. 執行後查看結果 CSV
Import-Csv C:\Temp\ad-bulk-result.csv | Format-Table

# 4. 驗證帳號已建立
"jdoe","mchen" | ForEach-Object {
    Get-ADUser -Identity $_ -Properties Department, MemberOf |
        Select-Object Name, Department, @{N='Groups'; E={$_.MemberOf.Count}}
}
```

## 自我檢核

- [ ] 腳本支援 `-WhatIf`（加 `[CmdletBinding(SupportsShouldProcess)]`）
- [ ] 單一帳號失敗不中斷整批
- [ ] 重複帳號偵測並標記 SKIP
- [ ] 結果輸出到 CSV，方便後續審查

→ [Ch 28 自訂模組開發](./28-custom-modules.md)
