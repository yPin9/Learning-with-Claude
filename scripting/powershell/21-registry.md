# Ch 21 — 登錄檔操作

> 目標：用 PowerShell 讀寫 Windows 登錄檔，取代 `regedit` 和 `reg.exe`，能批次部署登錄檔設定。

## Registry Provider

PowerShell 把登錄檔抽象成 PSDrive，用和檔案系統幾乎一樣的語法操作：

```
HKLM:  →  HKEY_LOCAL_MACHINE
HKCU:  →  HKEY_CURRENT_USER
HKCR:  →  HKEY_CLASSES_ROOT（需要 mount）
HKU:   →  HKEY_USERS（需要 mount）
```

```powershell
# 確認可用的 Registry PSDrive
Get-PSDrive -PSProvider Registry

# 切換到登錄檔路徑（互動用）
Set-Location HKLM:\SOFTWARE\Microsoft
Get-ChildItem   # 列出子機碼
```

## 讀取登錄值

```powershell
# 讀取整個機碼（Key）的所有值（Value）
Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"

# 讀取特定值
(Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion").ProductName
# Windows 11 Pro

# 讀取單一值（用 -Name 更乾淨）
Get-ItemPropertyValue -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" `
    -Name "CurrentBuild"

# 確認機碼是否存在
Test-Path "HKLM:\SOFTWARE\MyApp"
```

## 建立和修改

```powershell
# 建立機碼（目錄）
New-Item -Path "HKCU:\SOFTWARE\MyApp" -Force
New-Item -Path "HKCU:\SOFTWARE\MyApp\Config" -Force

# 建立或設定值
New-ItemProperty -Path "HKCU:\SOFTWARE\MyApp" `
    -Name "Version" `
    -Value "1.0.0" `
    -PropertyType String

# 修改現有值
Set-ItemProperty -Path "HKCU:\SOFTWARE\MyApp" `
    -Name "Version" `
    -Value "2.0.0"

# 建立或更新（如果不知道是否存在，用 -Force）
New-ItemProperty -Path "HKCU:\SOFTWARE\MyApp" `
    -Name "Debug" `
    -Value 1 `
    -PropertyType DWord `
    -Force
```

## 登錄值的資料型別

| PropertyType | 說明 | .NET 型別 |
|-------------|------|-----------|
| `String` | REG_SZ | [string] |
| `ExpandString` | REG_EXPAND_SZ（%var% 展開） | [string] |
| `MultiString` | REG_MULTI_SZ（多行） | [string[]] |
| `DWord` | REG_DWORD（32 位元整數） | [int] |
| `QWord` | REG_QWORD（64 位元整數） | [long] |
| `Binary` | REG_BINARY | [byte[]] |

```powershell
# 不同型別的範例
New-ItemProperty -Path "HKCU:\SOFTWARE\MyApp" -Name "Name"    -Value "MyApp"     -PropertyType String
New-ItemProperty -Path "HKCU:\SOFTWARE\MyApp" -Name "Count"   -Value 42          -PropertyType DWord
New-ItemProperty -Path "HKCU:\SOFTWARE\MyApp" -Name "Tags"    -Value @("a","b")  -PropertyType MultiString
New-ItemProperty -Path "HKCU:\SOFTWARE\MyApp" -Name "AppPath" -Value "%AppData%\MyApp" -PropertyType ExpandString
```

## 刪除

```powershell
# 刪除單一值
Remove-ItemProperty -Path "HKCU:\SOFTWARE\MyApp" -Name "Debug"

# 刪除整個機碼（和它的所有子機碼、值）
Remove-Item -Path "HKCU:\SOFTWARE\MyApp" -Recurse -Force
```

## 備份和還原

```powershell
# 備份到 .reg 檔（用 reg.exe，PowerShell 目前沒有對應 cmdlet）
reg export "HKCU\SOFTWARE\MyApp" C:\Backup\myapp.reg

# 還原
reg import C:\Backup\myapp.reg

# 備份到 CliXml（PS 內部格式，保留型別資訊）
Get-ItemProperty "HKCU:\SOFTWARE\MyApp" |
    Export-CliXml C:\Backup\myapp_settings.xml
```

## 常用的登錄操作

```powershell
# 1. 查看已安裝的軟體
Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" |
    ForEach-Object { Get-ItemProperty $_.PsPath } |
    Where-Object { $_.DisplayName } |
    Select-Object DisplayName, DisplayVersion, Publisher |
    Sort-Object DisplayName |
    Format-Table -AutoSize

# 2. 查看開機自啟動程式
Get-ItemProperty "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"

# 3. 設定 IE/Edge 代理（示範，實際可能有更好的方式）
Set-ItemProperty -Path "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings" `
    -Name "ProxyEnable" -Value 1 -Type DWord
Set-ItemProperty -Path "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings" `
    -Name "ProxyServer" -Value "proxy.corp.com:8080" -Type String
```

## 批次部署設定的模板

```powershell
# 定義要設定的一組登錄值，然後統一套用
$regSettings = @(
    @{ Path = "HKCU:\SOFTWARE\MyApp"; Name = "LogLevel";  Value = 2;       Type = "DWord"  }
    @{ Path = "HKCU:\SOFTWARE\MyApp"; Name = "ServerUrl";  Value = "https://api.corp.com"; Type = "String" }
    @{ Path = "HKCU:\SOFTWARE\MyApp"; Name = "MaxRetries"; Value = 3;       Type = "DWord"  }
)

foreach ($s in $regSettings) {
    # 確保 Key 存在
    if (-not (Test-Path $s.Path)) {
        New-Item $s.Path -Force | Out-Null
    }

    New-ItemProperty -Path $s.Path `
        -Name $s.Name `
        -Value $s.Value `
        -PropertyType $s.Type `
        -Force | Out-Null

    Write-Verbose "設定：$($s.Path)\$($s.Name) = $($s.Value)"
}
```

## 動手練習

```powershell
# 1. 建立一個「應用程式設定」機碼，寫入幾個值，然後讀回來確認
$appKey = "HKCU:\SOFTWARE\PSPractice"
New-Item $appKey -Force | Out-Null
New-ItemProperty $appKey -Name "Author"  -Value "你的名字" -PropertyType String -Force | Out-Null
New-ItemProperty $appKey -Name "Version" -Value 1          -PropertyType DWord  -Force | Out-Null

$settings = Get-ItemProperty $appKey
Write-Host "Author:  $($settings.Author)"
Write-Host "Version: $($settings.Version)"

# 清理
Remove-Item $appKey -Recurse -Force
```

## 自我檢核

- [ ] 能分清楚機碼（Key）和值（Value）的概念
- [ ] 知道 `-PropertyType DWord` 對應 REG_DWORD
- [ ] 能用 `Test-Path` 判斷機碼是否存在，不存在時用 `-Force` 建立
- [ ] 知道要備份整棵子樹要用 `reg export`（PS 沒有直接對應 cmdlet）

→ [Ch 22 CIM / WMI 查詢](./22-cim-wmi.md)
