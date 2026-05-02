# 練習 B — 系統健康報告腳本

> 目標：整合 Part 4（Ch 18–23）的知識，建立一個收集 CPU / 記憶體 / 磁碟 / 服務狀態 / 近期錯誤的完整系統健康報告，輸出成 HTML。

## 任務規格

| 項目 | 說明 |
|------|------|
| 輸入 | 無（或可選 `-ComputerName`） |
| 收集項目 | CPU 使用率、記憶體用量、磁碟空間、異常服務、近期錯誤事件 |
| 輸出 | HTML 報告 + Console 摘要 |
| 警示顏色 | 磁碟超過 80% → 紅色，60-80% → 橙色 |
| 異常服務 | 自動啟動但目前未執行的服務 |
| 近期錯誤 | 過去 24 小時的 Error 級別事件（前 20 筆）|

## 期望輸出

```
=== 系統健康報告 ===
電腦：WORKSTATION01  時間：2024-01-15 09:30:00

[CPU] 使用率：12.3%
[記憶體] 已用：6.2 GB / 16.0 GB (38.8%)
[磁碟]
  C: 已用 45.2 GB / 237.5 GB (19.0%)  [OK]
  D: 已用 890.1 GB / 931.5 GB (95.6%) [警告!]

[異常服務] 找到 2 個自動啟動但未執行的服務：
  - wuauserv (Windows Update)
  - bits (Background Intelligent Transfer Service)

[近期錯誤] 過去 24 小時：15 筆

HTML 報告：C:\Temp\health-report.html
```

## 實作步驟建議

### Step 1：收集資料

```powershell
# CPU 使用率（取樣 2 秒平均）
function Get-CpuUsage {
    $counters = Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 2 -MaxSamples 1
    [Math]::Round($counters.CounterSamples.CookedValue, 1)
}

# 記憶體
function Get-MemoryInfo {
    $os = Get-CimInstance Win32_OperatingSystem
    $total = $os.TotalVisibleMemorySize * 1KB
    $free  = $os.FreePhysicalMemory * 1KB
    [PSCustomObject]@{
        TotalGB   = [Math]::Round($total / 1GB, 1)
        UsedGB    = [Math]::Round(($total - $free) / 1GB, 1)
        FreeGB    = [Math]::Round($free / 1GB, 1)
        UsedPct   = [Math]::Round(($total - $free) / $total * 100, 1)
    }
}
```

### Step 2：磁碟狀態（含警示）

```powershell
function Get-DiskStatus {
    Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
        $usedPct = [Math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100, 1)
        [PSCustomObject]@{
            Drive   = $_.DeviceID
            TotalGB = [Math]::Round($_.Size / 1GB, 1)
            UsedGB  = [Math]::Round(($_.Size - $_.FreeSpace) / 1GB, 1)
            FreeGB  = [Math]::Round($_.FreeSpace / 1GB, 1)
            UsedPct = $usedPct
            Status  = if ($usedPct -ge 90) { "CRITICAL" }
                      elseif ($usedPct -ge 80) { "WARNING" }
                      else { "OK" }
        }
    }
}
```

### Step 3：異常服務

```powershell
function Get-AbnormalServices {
    Get-Service |
        Where-Object { $_.StartType -eq "Automatic" -and $_.Status -eq "Stopped" } |
        Select-Object Name, DisplayName, Status
}
```

### Step 4：近期錯誤

```powershell
function Get-RecentErrors {
    param([int]$Hours = 24)
    Get-WinEvent -FilterHashtable @{
        LogName   = @('System', 'Application')
        Level     = @(1, 2)
        StartTime = (Get-Date).AddHours(-$Hours)
    } -ErrorAction SilentlyContinue |
        Select-Object TimeCreated, Id, LevelDisplayName, ProviderName,
            @{N='Message'; E={ ($_.Message -split "`n")[0] }}
}
```

### Step 5：整合 HTML 報告

HTML 報告要有：
- 整體摘要（機器名稱、產生時間）
- 磁碟使用率進度條（用 CSS）
- 異常服務表格
- 錯誤事件表格

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作</summary>

```powershell
# Invoke-HealthReport.ps1
[CmdletBinding()]
param(
    [string]$OutputPath = "C:\Temp\health-report.html",
    [int]$ErrorHours = 24
)

$ErrorActionPreference = "Stop"

function Get-CpuUsage {
    $c = Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 2
    [Math]::Round(($c.CounterSamples | Measure-Object CookedValue -Average).Average, 1)
}

function Get-MemInfo {
    $os = Get-CimInstance Win32_OperatingSystem
    $total = $os.TotalVisibleMemorySize * 1KB
    $free  = $os.FreePhysicalMemory * 1KB
    [PSCustomObject]@{
        TotalGB = [Math]::Round($total/1GB,1)
        UsedGB  = [Math]::Round(($total-$free)/1GB,1)
        UsedPct = [Math]::Round(($total-$free)/$total*100,1)
    }
}

try {
    $hostname = $env:COMPUTERNAME
    $genTime  = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "`n=== 系統健康報告 ===" -ForegroundColor Cyan
    Write-Host "電腦：$hostname  時間：$genTime"

    Write-Verbose "收集 CPU..."
    $cpu = Get-CpuUsage
    Write-Host "[CPU] 使用率：${cpu}%"

    Write-Verbose "收集記憶體..."
    $mem = Get-MemInfo
    Write-Host "[記憶體] 已用：$($mem.UsedGB) GB / $($mem.TotalGB) GB ($($mem.UsedPct)%)"

    Write-Verbose "收集磁碟..."
    $disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
        $pct = [Math]::Round(($_.Size-$_.FreeSpace)/$_.Size*100,1)
        $status = if($pct -ge 90){"CRITICAL"}elseif($pct -ge 80){"WARNING"}else{"OK"}
        $color  = if($pct -ge 90){"red"}elseif($pct -ge 80){"orange"}else{"green"}
        [PSCustomObject]@{
            Drive=$_.DeviceID; TotalGB=[Math]::Round($_.Size/1GB,1)
            UsedGB=[Math]::Round(($_.Size-$_.FreeSpace)/1GB,1)
            FreeGB=[Math]::Round($_.FreeSpace/1GB,1)
            UsedPct=$pct; Status=$status; Color=$color
        }
    }
    $disks | ForEach-Object {
        $c = if($_.Status -eq "OK"){"Green"}elseif($_.Status -eq "WARNING"){"Yellow"}else{"Red"}
        Write-Host "  $($_.Drive) 已用 $($_.UsedGB)GB/$($_.TotalGB)GB ($($_.UsedPct)%) [$($_.Status)]" -ForegroundColor $c
    }

    Write-Verbose "收集服務..."
    $badSvc = Get-Service | Where-Object { $_.StartType -eq "Automatic" -and $_.Status -eq "Stopped" }
    Write-Host "[異常服務] $($badSvc.Count) 個自動啟動但未執行"
    $badSvc | ForEach-Object { Write-Host "  - $($_.Name) ($($_.DisplayName))" -ForegroundColor Yellow }

    Write-Verbose "收集事件..."
    $errors = Get-WinEvent -FilterHashtable @{
        LogName=@('System','Application'); Level=@(1,2)
        StartTime=(Get-Date).AddHours(-$ErrorHours)
    } -ErrorAction SilentlyContinue
    Write-Host "[事件] 過去 ${ErrorHours}h 錯誤：$($errors.Count) 筆"

    # 生成 HTML
    $diskRows = $disks | ForEach-Object {
        $bar = "<div style='background:#ddd;width:200px;display:inline-block'><div style='background:$($_.Color);width:$($_.UsedPct)%;height:16px'></div></div>"
        "<tr><td>$($_.Drive)</td><td>$($_.TotalGB) GB</td><td>$($_.UsedGB) GB</td><td>$($_.FreeGB) GB</td><td>$bar $($_.UsedPct)%</td><td style='color:$($_.Color)'>$($_.Status)</td></tr>"
    }

    $svcRows = $badSvc | ForEach-Object {
        "<tr><td>$($_.Name)</td><td>$($_.DisplayName)</td></tr>"
    }

    $errRows = ($errors | Select-Object -First 20) | ForEach-Object {
        "<tr><td>$($_.TimeCreated)</td><td>$($_.Id)</td><td>$($_.ProviderName)</td><td>$(($_.Message -split '\n')[0])</td></tr>"
    }

    $css = "body{font-family:Arial;margin:2em}h2{color:#4472C4}table{border-collapse:collapse;width:100%;margin:1em 0}th,td{border:1px solid #ccc;padding:8px}th{background:#4472C4;color:#fff}tr:nth-child(even){background:#f2f2f2}.warn{color:orange}.crit{color:red}"

    $html = @"
<!DOCTYPE html><html><head><meta charset="utf-8"><title>健康報告 - $hostname</title><style>$css</style></head>
<body>
<h1>系統健康報告</h1>
<p>電腦：<b>$hostname</b> | 產生時間：<b>$genTime</b></p>
<table><tr><th>項目</th><th>數值</th></tr>
<tr><td>CPU 使用率</td><td>${cpu}%</td></tr>
<tr><td>記憶體</td><td>$($mem.UsedGB) / $($mem.TotalGB) GB ($($mem.UsedPct)%)</td></tr>
</table>
<h2>磁碟狀態</h2><table><tr><th>磁碟</th><th>總容量</th><th>已用</th><th>空餘</th><th>使用率</th><th>狀態</th></tr>
$($diskRows -join '')</table>
<h2>異常服務（自動啟動但未執行）</h2>
$(if($badSvc.Count -eq 0){"<p>無異常服務</p>"}else{"<table><tr><th>服務名稱</th><th>顯示名稱</th></tr>$($svcRows -join '')</table>"})
<h2>近期錯誤事件（過去 ${ErrorHours}h，前 20 筆）</h2>
$(if($errors.Count -eq 0){"<p>無錯誤事件</p>"}else{"<table><tr><th>時間</th><th>ID</th><th>來源</th><th>訊息</th></tr>$($errRows -join '')</table>"})
</body></html>
"@
    New-Item (Split-Path $OutputPath -Parent) -ItemType Directory -Force | Out-Null
    $html | Out-File $OutputPath -Encoding utf8
    Write-Host "`nHTML 報告：$OutputPath" -ForegroundColor Green
    Start-Process $OutputPath

} catch {
    Write-Error "報告生成失敗：$($_.Exception.Message)"
    exit 1
}
```

</details>

## 測試用例

```powershell
# 基本執行
.\Invoke-HealthReport.ps1

# 指定輸出路徑
.\Invoke-HealthReport.ps1 -OutputPath "C:\Reports\$(Get-Date -Format yyyyMMdd).html"

# 只看過去 48 小時的錯誤
.\Invoke-HealthReport.ps1 -ErrorHours 48 -Verbose
```

## 自我檢核

- [ ] 磁碟警示顏色依據閾值（80% / 90%）正確顯示
- [ ] 能找出「自動啟動但停止」的服務
- [ ] HTML 報告在瀏覽器裡版面正確
- [ ] 錯誤處理包住整個主邏輯

→ [Ch 24 PSRemoting 基礎](./24-psremoting-basics.md)
