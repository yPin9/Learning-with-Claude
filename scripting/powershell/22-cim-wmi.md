# Ch 22 — CIM / WMI 查詢

> 目標：用 `Get-CimInstance` 查詢硬體資訊、OS 狀態、熱修補清單，理解 CIM 和 WMI 的關係。

## WMI vs CIM：用哪個？

**WMI**（Windows Management Instrumentation）是舊標準，cmdlet 是 `Get-WmiObject`。
**CIM**（Common Information Model）是新標準，cmdlet 是 `Get-CimInstance`。

```
Get-WmiObject   →  只能查本機或用 DCOM 遠端（防火牆難設）
Get-CimInstance →  本機或用 WSMan/WinRM 遠端（較現代，推薦使用）
```

PS 3.0+ 開始，**一律用 `Get-CimInstance`**，`Get-WmiObject` 在 PS 6+ 已被移除。

## 基本語法

```powershell
# 查詢 WMI 類別
Get-CimInstance -ClassName Win32_OperatingSystem
Get-CimInstance Win32_OperatingSystem   # -ClassName 可以省略

# 搜尋有哪些 WMI 類別
Get-CimClass -Namespace "root/cimv2" | Where-Object { $_.CimClassName -like "Win32_*" }
```

## 常用查詢

### 作業系統

```powershell
$os = Get-CimInstance Win32_OperatingSystem

$os.Caption          # Windows 11 Pro
$os.Version          # 10.0.22631
$os.BuildNumber      # 22631
$os.OSArchitecture   # 64-bit
$os.LastBootUpTime   # 上次開機時間
$os.FreePhysicalMemory   # 剩餘記憶體（KB）

# 計算開機時長
$uptime = (Get-Date) - $os.LastBootUpTime
"已開機：$([int]$uptime.TotalHours) 小時"
```

### 硬體資訊

```powershell
# CPU
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed

# 記憶體（每條實體記憶體條）
Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator,
    @{N='CapacityGB'; E={ [int]($_.Capacity / 1GB) }},
    Speed, Manufacturer

# 總記憶體
$totalMem = (Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum).Sum / 1GB
"總記憶體：$totalMem GB"

# 磁碟
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |  # 3 = 本機磁碟
    Select-Object DeviceID,
        @{N='TotalGB'; E={ [Math]::Round($_.Size / 1GB, 1) }},
        @{N='FreeGB';  E={ [Math]::Round($_.FreeSpace / 1GB, 1) }},
        @{N='Used%';   E={ [Math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100, 1) }}

# 實體磁碟
Get-CimInstance Win32_DiskDrive | Select-Object Model, Size, InterfaceType
```

### 電腦資訊

```powershell
# 基本電腦資訊
$comp = Get-CimInstance Win32_ComputerSystem
$comp.Name           # 電腦名稱
$comp.Domain         # 加入的網域
$comp.Manufacturer   # 製造商（Dell, HP, Lenovo...）
$comp.Model          # 型號
$comp.TotalPhysicalMemory   # 總記憶體

# BIOS 資訊
Get-CimInstance Win32_BIOS | Select-Object Manufacturer, Name, Version, ReleaseDate
```

### 熱修補（Hot Fix）

```powershell
# 列出已安裝的 KB
Get-CimInstance Win32_QuickFixEngineering |
    Select-Object HotFixID, Description, InstalledOn |
    Sort-Object InstalledOn -Descending

# 確認特定 KB 是否已安裝
Get-CimInstance Win32_QuickFixEngineering -Filter "HotFixID='KB5034765'"
```

## WQL：WMI 查詢語言

CIM 支援類 SQL 的過濾語法（WQL），比在 PS 裡用 Where-Object 更快（在查詢源頭就過濾）：

```powershell
# -Filter 使用 WQL 語法
Get-CimInstance Win32_Process -Filter "Name='notepad.exe'"
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3 AND FreeSpace < 10737418240"  # < 10GB

# WQL 語法和 SQL 類似，但只有 = < > AND OR NOT LIKE
# LIKE 用 % 萬用字元：Name LIKE '%service%'
```

## 遠端查詢

```powershell
# 遠端查詢（需要 WinRM）
Get-CimInstance Win32_OperatingSystem -ComputerName "server01"

# 批次查詢
$servers = @("server01", "server02", "server03")
Get-CimInstance Win32_OperatingSystem -ComputerName $servers |
    Select-Object PSComputerName, Caption, LastBootUpTime

# 用 CimSession（適合多次查詢同一台機器，減少連線次數）
$session = New-CimSession -ComputerName "server01"
Get-CimInstance Win32_Processor      -CimSession $session
Get-CimInstance Win32_LogicalDisk    -CimSession $session
Get-CimInstance Win32_OperatingSystem -CimSession $session
Remove-CimSession $session
```

## 系統庫存報告

```powershell
function Get-SystemInventory {
    param([string[]]$ComputerName = "localhost")

    foreach ($comp in $ComputerName) {
        $os    = Get-CimInstance Win32_OperatingSystem -ComputerName $comp
        $sys   = Get-CimInstance Win32_ComputerSystem  -ComputerName $comp
        $cpu   = Get-CimInstance Win32_Processor       -ComputerName $comp
        $disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ComputerName $comp

        [PSCustomObject]@{
            Computer  = $comp
            OS        = $os.Caption
            Build     = $os.BuildNumber
            Uptime    = [int]((Get-Date) - $os.LastBootUpTime).TotalDays
            CPU       = $cpu.Name
            Cores     = $cpu.NumberOfCores
            MemGB     = [Math]::Round($sys.TotalPhysicalMemory / 1GB, 1)
            DiskInfo  = ($disks | ForEach-Object {
                "$($_.DeviceID) $([int]($_.Size/1GB))G/$([int]($_.FreeSpace/1GB))G 空"
            }) -join "; "
        }
    }
}

Get-SystemInventory | Format-List
```

## 動手練習

```powershell
# 建立一份本機完整硬體報告
Write-Host "=== 系統資訊報告 ===" -ForegroundColor Cyan

$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem

Write-Host "電腦：$($cs.Name)  型號：$($cs.Manufacturer) $($cs.Model)"
Write-Host "OS：$($os.Caption) Build $($os.BuildNumber)"
Write-Host "記憶體：$([Math]::Round($cs.TotalPhysicalMemory/1GB,1)) GB"

Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    ForEach-Object {
        $used = [Math]::Round(($_.Size - $_.FreeSpace) / 1GB, 1)
        $free = [Math]::Round($_.FreeSpace / 1GB, 1)
        $pct  = [Math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100)
        Write-Host "磁碟 $($_.DeviceID) 已用 ${used}GB / 空餘 ${free}GB ($pct%)"
    }
```

## 自我檢核

- [ ] 知道 `Get-CimInstance` 是 `Get-WmiObject` 的繼承者，優先使用前者
- [ ] 能用 `-Filter` 做 WQL 過濾（在查詢源頭過濾比 Where-Object 快）
- [ ] 知道 `Win32_QuickFixEngineering` 查熱修補
- [ ] 能用 `CimSession` 對同一台遠端主機做多次查詢

→ [Ch 23 事件日誌](./23-event-log.md)
