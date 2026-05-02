# Ch 28 — 自訂模組開發

> 目標：把腳本組織成可重用的模組，建立 `.psm1` 和 `.psd1`，掌握公開/私有函式，發布到 PSGallery。

## 為什麼要模組

把相關函式打包成模組：
- 一個 `Import-Module` 就能用，不需要 dot-source 每個腳本
- 可以控制哪些函式是公開 API（`Export-ModuleMember`）
- 版本管理，呼叫方可以指定需要的最低版本
- 可以發布到 PSGallery 讓整個組織使用

## 模組結構

```
MyOpsTools/
├── MyOpsTools.psd1        # Module Manifest（元資料）
├── MyOpsTools.psm1        # 主要模組程式碼
├── Public/
│   ├── Get-DiskReport.ps1
│   ├── Invoke-HealthCheck.ps1
│   └── ...
└── Private/
    ├── Format-HtmlTable.ps1    # 內部函式，不對外暴露
    └── ...
```

## 建立 .psm1

最簡單的模組就是一個 `.psm1` 檔，載入 Public 和 Private 目錄的所有函式：

```powershell
# MyOpsTools.psm1

# 載入所有函式
$publicFunctions  = @(Get-ChildItem -Path "$PSScriptRoot\Public\*.ps1"  -ErrorAction SilentlyContinue)
$privateFunctions = @(Get-ChildItem -Path "$PSScriptRoot\Private\*.ps1" -ErrorAction SilentlyContinue)

foreach ($func in ($publicFunctions + $privateFunctions)) {
    . $func.FullName   # dot-source 載入
}

# 只匯出 Public 的函式
Export-ModuleMember -Function $publicFunctions.BaseName
```

或者直接把函式寫在 `.psm1` 裡（小模組適合）：

```powershell
# MyOpsTools.psm1

function Get-DiskHealth {
    param([int]$WarnPct = 80, [int]$CritPct = 90)
    Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
        $pct = [Math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100, 1)
        [PSCustomObject]@{
            Drive  = $_.DeviceID
            UsedPct = $pct
            Status = if ($pct -ge $CritPct) { "CRITICAL" }
                     elseif ($pct -ge $WarnPct) { "WARNING" }
                     else { "OK" }
        }
    }
}

function Invoke-HealthCheck {
    # 呼叫內部函式
    Get-DiskHealth | Where-Object { $_.Status -ne "OK" }
}

# ← 沒有 Export-ModuleMember 的話，預設所有函式都公開
```

## 建立 Module Manifest（.psd1）

`.psd1` 是模組的元資料描述檔，有了它才能做版本管理和相依性宣告：

```powershell
New-ModuleManifest `
    -Path ".\MyOpsTools\MyOpsTools.psd1" `
    -ModuleVersion "1.0.0" `
    -Author "Your Name" `
    -Description "系統維運自動化工具集" `
    -RootModule "MyOpsTools.psm1" `
    -FunctionsToExport @("Get-DiskHealth", "Invoke-HealthCheck") `
    -RequiredModules @() `
    -PowerShellVersion "7.0"
```

手動編輯 `.psd1` 的常用欄位：

```powershell
@{
    ModuleVersion     = '1.0.0'
    GUID              = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
    Author            = 'Your Name'
    Description       = '說明'
    PowerShellVersion = '7.0'
    RootModule        = 'MyOpsTools.psm1'
    FunctionsToExport = @('Get-DiskHealth', 'Invoke-HealthCheck')
    AliasesToExport   = @()
    VariablesToExport = @()
    PrivateData       = @{
        PSData = @{
            Tags    = @('SysAdmin', 'Automation')
            License = 'MIT'
        }
    }
}
```

## 安裝模組（本機使用）

PS 模組要放在 `$env:PSModulePath` 裡的其中一個路徑才能自動找到：

```powershell
# 查看模組路徑
$env:PSModulePath -split ";"

# 典型路徑：
# C:\Users\you\Documents\PowerShell\Modules    ← 個人（推薦）
# C:\Program Files\PowerShell\7\Modules        ← 系統全域

# 複製模組到個人路徑
$moduleDest = "$([Environment]::GetFolderPath('MyDocuments'))\PowerShell\Modules\MyOpsTools"
Copy-Item .\MyOpsTools -Destination $moduleDest -Recurse -Force

# 驗證
Get-Module -ListAvailable -Name MyOpsTools
Import-Module MyOpsTools
Get-Command -Module MyOpsTools
```

## 版本更新流程

```powershell
# 1. 修改程式碼
# 2. 更新 .psd1 的 ModuleVersion（語意化版本：MAJOR.MINOR.PATCH）
# 3. 重新載入模組（移除舊版再匯入）
Remove-Module MyOpsTools -Force
Import-Module MyOpsTools -Force

# 強制重新載入
Import-Module MyOpsTools -Force
```

## 測試模組

寫 Pester 測試（PowerShell 的測試框架）：

```powershell
# MyOpsTools.Tests.ps1
Describe "Get-DiskHealth" {
    It "回傳每個磁碟的物件" {
        $result = Get-DiskHealth
        $result | Should -Not -BeNullOrEmpty
    }

    It "物件有 Drive, UsedPct, Status 屬性" {
        $result = Get-DiskHealth | Select-Object -First 1
        $result.Drive  | Should -Not -BeNullOrEmpty
        $result.UsedPct | Should -BeOfType [double]
    }
}

# 執行測試
Invoke-Pester .\MyOpsTools.Tests.ps1
```

## 發布到 PSGallery

```powershell
# 先取得 API Key（在 powershellgallery.com 網站申請）
$apiKey = "your-api-key-here"

# 發布
Publish-Module `
    -Name "MyOpsTools" `
    -NuGetApiKey $apiKey `
    -Repository PSGallery `
    -Verbose

# 其他人安裝你的模組
Install-Module -Name MyOpsTools -Repository PSGallery
```

## 動手練習

建立一個小模組 `PSUtils`：

```powershell
# 建立結構
New-Item .\PSUtils -ItemType Directory
New-Item .\PSUtils\Public -ItemType Directory

# 主模組檔
@'
$public = @(Get-ChildItem "$PSScriptRoot\Public\*.ps1" -EA 0)
$public | ForEach-Object { . $_.FullName }
Export-ModuleMember -Function $public.BaseName
'@ | Set-Content .\PSUtils\PSUtils.psm1

# 一個公開函式
@'
function Get-SystemSummary {
    $os = Get-CimInstance Win32_OperatingSystem
    [PSCustomObject]@{
        Computer = $env:COMPUTERNAME
        OS       = $os.Caption
        MemGB    = [Math]::Round($os.TotalVisibleMemorySize/1MB, 1)
        Uptime   = [int]((Get-Date) - $os.LastBootUpTime).TotalHours
    }
}
'@ | Set-Content .\PSUtils\Public\Get-SystemSummary.ps1

# 建立 manifest
New-ModuleManifest -Path .\PSUtils\PSUtils.psd1 -ModuleVersion "1.0.0" `
    -RootModule PSUtils.psm1 -FunctionsToExport @("Get-SystemSummary")

# 測試
Import-Module .\PSUtils\PSUtils.psd1 -Force
Get-SystemSummary
```

## 自我檢核

- [ ] 理解 `.psm1` 是程式碼，`.psd1` 是元資料
- [ ] 知道 `Export-ModuleMember` 控制哪些函式對外公開
- [ ] 模組要放在 `$env:PSModulePath` 的其中一個路徑才能被 `Import-Module` 找到
- [ ] 知道用 `-Force` 強制重新載入已修改的模組

→ [Ch 29 REST API 整合與安全實踐](./29-rest-api-and-security.md)
