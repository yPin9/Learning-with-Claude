# Final Project — 系統維運自動化套件

> 目標：整合整個課程的知識，建立一個可真實部署的 PowerShell 模組 `SysOpsToolkit`，包含健康監控、遠端批次執行、報告產出、Webhook 通知功能。

## 專案規格

### 模組結構

```
SysOpsToolkit/
├── SysOpsToolkit.psd1
├── SysOpsToolkit.psm1
├── Public/
│   ├── Invoke-HealthCheck.ps1      # 健康檢查（本機 or 遠端）
│   ├── Get-SystemInventory.ps1     # 系統庫存收集
│   ├── Invoke-PatchStatus.ps1      # 熱修補狀態
│   └── Send-AlertNotification.ps1  # 通知發送
└── Private/
    ├── Get-DiskStatus.ps1
    ├── Get-ServiceStatus.ps1
    └── ConvertTo-HtmlReport.ps1
```

### 功能矩陣

| 函式 | 輸入 | 輸出 |
|------|------|------|
| `Invoke-HealthCheck` | 電腦名稱（清單）| 健康狀態物件 |
| `Get-SystemInventory` | 電腦名稱（清單）| 庫存 CSV |
| `Invoke-PatchStatus` | 電腦名稱（清單）| 熱修補狀態 |
| `Send-AlertNotification` | 訊息、Webhook URL | 發送通知 |

## 實作步驟建議

### Step 1：Private 函式

先把共用的內部函式寫好：

**`Private/Get-DiskStatus.ps1`**

```powershell
function Get-DiskStatusInternal {
    [CmdletBinding()]
    param(
        [int]$WarnPct = 80,
        [int]$CritPct = 90
    )
    process {
        Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
            if ($_.Size -eq 0) { return }
            $usedPct = [Math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100, 1)
            [PSCustomObject]@{
                Drive   = $_.DeviceID
                TotalGB = [Math]::Round($_.Size / 1GB, 1)
                UsedGB  = [Math]::Round(($_.Size - $_.FreeSpace) / 1GB, 1)
                FreeGB  = [Math]::Round($_.FreeSpace / 1GB, 1)
                UsedPct = $usedPct
                Status  = if ($usedPct -ge $CritPct) { "CRITICAL" }
                          elseif ($usedPct -ge $WarnPct) { "WARNING" }
                          else { "OK" }
            }
        }
    }
}
```

**`Private/ConvertTo-HtmlReport.ps1`**

```powershell
function ConvertTo-HtmlReport {
    param(
        [string]$Title,
        [string]$Content,
        [string]$GeneratedBy = $env:COMPUTERNAME
    )
    $css = @"
body{font-family:Segoe UI,Arial;margin:2em;background:#f8f9fa}
h1{color:#2c3e50}h2{color:#34495e;border-bottom:2px solid #3498db}
table{border-collapse:collapse;width:100%;margin:1em 0}
th{background:#3498db;color:#fff;padding:10px}
td{border:1px solid #ddd;padding:8px}
tr:nth-child(even){background:#ecf0f1}
.ok{color:#27ae60;font-weight:bold}
.warning{color:#f39c12;font-weight:bold}
.critical{color:#e74c3c;font-weight:bold}
.badge{padding:2px 8px;border-radius:3px;color:#fff}
.badge-ok{background:#27ae60}.badge-warn{background:#f39c12}.badge-crit{background:#e74c3c}
"@
    @"
<!DOCTYPE html><html><head><meta charset="utf-8"><title>$Title</title>
<style>$css</style></head><body>
<h1>$Title</h1>
<p>產生時間：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | 來源：$GeneratedBy</p>
$Content
</body></html>
"@
}
```

### Step 2：Invoke-HealthCheck（核心功能）

```powershell
# Public/Invoke-HealthCheck.ps1
function Invoke-HealthCheck {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromPipeline)]
        [string[]]$ComputerName = @("localhost"),

        [int]$DiskWarnPct = 80,
        [int]$DiskCritPct = 90,

        [string]$ReportPath,

        [string]$WebhookUrl
    )

    $results = [System.Collections.Generic.List[object]]::new()
    $alerts  = [System.Collections.Generic.List[string]]::new()

    foreach ($computer in $ComputerName) {
        Write-Verbose "檢查：$computer"

        try {
            $sb = {
                param($warnPct, $critPct)

                $os   = Get-CimInstance Win32_OperatingSystem
                $cs   = Get-CimInstance Win32_ComputerSystem
                $disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"
                $badSvc = Get-Service | Where-Object {
                    $_.StartType -eq "Automatic" -and $_.Status -eq "Stopped"
                }

                $diskStatus = $disks | ForEach-Object {
                    if ($_.Size -gt 0) {
                        $pct = [Math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100, 1)
                        "$($_.DeviceID) ${pct}%"
                    }
                }

                $worstDisk = ($disks | Where-Object { $_.Size -gt 0 } | ForEach-Object {
                    [Math]::Round(($_.Size - $_.FreeSpace) / $_.Size * 100, 1)
                } | Measure-Object -Maximum).Maximum

                [PSCustomObject]@{
                    Computer   = $env:COMPUTERNAME
                    OS         = $os.Caption
                    UptimeHours = [int]((Get-Date) - $os.LastBootUpTime).TotalHours
                    MemTotalGB = [Math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
                    MemFreeGB  = [Math]::Round($os.FreePhysicalMemory / 1MB, 1)
                    WorstDiskPct = $worstDisk
                    DiskDetails = $diskStatus -join "; "
                    BadServices = $badSvc.Count
                    BadSvcList  = ($badSvc | Select-Object -ExpandProperty Name) -join "; "
                    DiskStatus  = if ($worstDisk -ge $critPct) { "CRITICAL" }
                                  elseif ($worstDisk -ge $warnPct) { "WARNING" }
                                  else { "OK" }
                }
            }

            if ($computer -eq "localhost" -or $computer -eq $env:COMPUTERNAME) {
                $result = & $sb $DiskWarnPct $DiskCritPct
            } else {
                $result = Invoke-Command -ComputerName $computer `
                    -ScriptBlock $sb `
                    -ArgumentList $DiskWarnPct, $DiskCritPct `
                    -ErrorAction Stop
            }

            $results.Add($result)

            if ($result.DiskStatus -ne "OK" -or $result.BadServices -gt 0) {
                $alertMsg = "$($result.Computer): Disk=$($result.DiskStatus) BadSvc=$($result.BadServices)"
                $alerts.Add($alertMsg)
                Write-Warning $alertMsg
            }

        } catch {
            $results.Add([PSCustomObject]@{
                Computer = $computer
                DiskStatus = "UNREACHABLE"
            })
            Write-Warning "$computer 無法連線：$($_.Exception.Message)"
        }
    }

    # 發送 Webhook 通知
    if ($WebhookUrl -and $alerts.Count -gt 0) {
        Send-AlertNotification -WebhookUrl $WebhookUrl `
            -Message "健康檢查警示：`n$($alerts -join "`n")"
    }

    # 產生 HTML 報告
    if ($ReportPath) {
        $tableRows = $results | ForEach-Object {
            $sc = switch ($_.DiskStatus) {
                "OK"          { "ok" }
                "WARNING"     { "warning" }
                "CRITICAL"    { "critical" }
                "UNREACHABLE" { "critical" }
            }
            "<tr>
                <td>$($_.Computer)</td>
                <td>$($_.OS)</td>
                <td>$($_.UptimeHours)h</td>
                <td>$($_.MemFreeGB) / $($_.MemTotalGB) GB</td>
                <td>$($_.DiskDetails)</td>
                <td>$($_.BadServices) ($($_.BadSvcList))</td>
                <td class='$sc'>$($_.DiskStatus)</td>
            </tr>"
        }

        $table = "<table><tr><th>電腦</th><th>OS</th><th>開機時長</th><th>記憶體</th><th>磁碟</th><th>異常服務</th><th>狀態</th></tr>$($tableRows -join '')</table>"
        $html = ConvertTo-HtmlReport -Title "系統健康報告" -Content "<h2>總覽</h2>$table"
        $html | Out-File $ReportPath -Encoding utf8
        Write-Host "報告：$ReportPath" -ForegroundColor Green
    }

    return $results
}
```

### Step 3：Send-AlertNotification

```powershell
# Public/Send-AlertNotification.ps1
function Send-AlertNotification {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$WebhookUrl,

        [Parameter(Mandatory)]
        [string]$Message,

        [string]$Title = "SysOpsToolkit 警示",
        [ValidateSet("info","warning","critical")]
        [string]$Severity = "warning"
    )

    $body = @{
        text        = "[$($Severity.ToUpper())] $Title"
        attachments = @(@{
            text  = $Message
            color = switch ($Severity) {
                "info"     { "good" }
                "warning"  { "warning" }
                "critical" { "danger" }
            }
        })
    } | ConvertTo-Json -Depth 3

    try {
        Invoke-RestMethod -Uri $WebhookUrl -Method Post `
            -Body $body -ContentType "application/json" -ErrorAction Stop
        Write-Verbose "通知已發送"
    } catch {
        Write-Warning "通知發送失敗：$($_.Exception.Message)"
    }
}
```

### Step 4：整合模組

```powershell
# SysOpsToolkit.psm1
$public  = @(Get-ChildItem "$PSScriptRoot\Public\*.ps1"  -EA 0)
$private = @(Get-ChildItem "$PSScriptRoot\Private\*.ps1" -EA 0)

($public + $private) | ForEach-Object { . $_.FullName }
Export-ModuleMember -Function $public.BaseName
```

```powershell
# SysOpsToolkit.psd1
New-ModuleManifest `
    -Path .\SysOpsToolkit\SysOpsToolkit.psd1 `
    -ModuleVersion "1.0.0" `
    -Author "你的名字" `
    -Description "系統維運自動化工具集" `
    -RootModule "SysOpsToolkit.psm1" `
    -FunctionsToExport @("Invoke-HealthCheck","Get-SystemInventory","Invoke-PatchStatus","Send-AlertNotification") `
    -PowerShellVersion "7.0"
```

## 測試用例

```powershell
# 安裝模組
Import-Module .\SysOpsToolkit\SysOpsToolkit.psd1 -Force

# 1. 本機健康檢查
Invoke-HealthCheck -Verbose

# 2. 產生 HTML 報告
Invoke-HealthCheck -ReportPath C:\Temp\health.html
Start-Process C:\Temp\health.html

# 3. 多台機器（如果有環境）
$servers = @("server01", "server02")
Invoke-HealthCheck -ComputerName $servers -ReportPath C:\Temp\cluster-health.html

# 4. 帶 Webhook 通知（需要 Slack/Teams Webhook URL）
# Invoke-HealthCheck -WebhookUrl "https://hooks.slack.com/..." -ReportPath C:\Temp\health.html

# 5. 組合：每天 6AM 跑健康檢查
$action = New-ScheduledTaskAction -Execute "pwsh.exe" `
    -Argument "-Command `"Import-Module C:\Scripts\SysOpsToolkit; Invoke-HealthCheck -ReportPath C:\Reports\$(Get-Date -Format yyyyMMdd).html`""
$trigger = New-ScheduledTaskTrigger -Daily -At "06:00"
Register-ScheduledTask -TaskName "DailyHealthCheck" -Action $action -Trigger $trigger -RunLevel Highest -Force
```

## 延伸挑戰

完成基本功能後，可以繼續擴充：

1. **`Get-SystemInventory`**：收集所有機器的 CPU、記憶體、磁碟、OS、已安裝軟體，匯出 CSV
2. **`Invoke-PatchStatus`**：查詢每台機器最後安裝 Windows Update 的時間，找出超過 30 天沒更新的機器
3. **差異比較**：比較本次和上次健康檢查的差異，只回報變化
4. **Teams 整合**：送 Adaptive Card 而非純文字 Webhook

## 自我檢核

- [ ] 模組結構正確：`psd1` + `psm1` + `Public/` + `Private/`
- [ ] `Invoke-HealthCheck` 支援本機和遠端，回傳標準化物件
- [ ] HTML 報告有顏色標示（OK/WARNING/CRITICAL）
- [ ] `Send-AlertNotification` 支援不同 severity
- [ ] 整個模組能用一個 `Import-Module` 載入並使用

**恭喜完成 PowerShell 全面課程！** 你現在掌握了從語法基礎到系統管理、遠端管理、AD、模組開發的完整工具鏈，足以處理真實環境的維運自動化工作。
