# 練習 A — 日誌分析腳本

> 目標：把 Part 1–3（Ch 0–17）學到的東西拼起來，寫一個能解析 Windows 事件日誌 CSV，做統計分析，輸出 HTML 報告的腳本。

## 任務規格

| 項目 | 說明 |
|------|------|
| 輸入 | Windows 事件日誌 CSV（自動生成測試資料）|
| 處理 | 解析、篩選、統計、分組 |
| 輸出 | HTML 報告（含摘要表格和錯誤明細）|
| 參數 | `-LogPath`、`-OutputPath`、`-Since`（幾天內）|
| 錯誤處理 | 完整 try/catch，有意義的錯誤訊息 |

## 期望輸出

```
=== 日誌分析報告 ===
分析區間：2024-01-10 ~ 2024-01-15
總計事件：500 筆

Level 統計：
  INFO:    350 筆 (70%)
  WARN:     95 筆 (19%)
  ERROR:    55 筆 (11%)

來源統計（前 5）：
  Application:  200 筆
  System:       150 筆
  ...

HTML 報告已儲存到：C:\Temp\log-report.html
```

## 實作步驟建議

### Step 1：建立測試日誌資料

先建立假資料，不用依賴真實環境：

```powershell
function New-TestLogData {
    param(
        [int]$Count = 500,
        [string]$OutputPath = "C:\Temp\test-events.csv"
    )

    $levels   = @("INFO","INFO","INFO","WARN","ERROR") # 7:2:1 的比例
    $sources  = @("Application","System","Security","Service","Network")
    $messages = @(
        "服務啟動成功",
        "資料庫連線正常",
        "記憶體用量超過閾值",
        "磁碟空間不足",
        "認證失敗：帳號已鎖定",
        "網路逾時",
        "排程工作執行完成",
        "設定檔讀取失敗"
    )

    $start = (Get-Date).AddDays(-7)

    $events = 1..$Count | ForEach-Object {
        $ts = $start.AddMinutes((Get-Random -Minimum 0 -Maximum 10080))
        [PSCustomObject]@{
            Timestamp  = $ts.ToString("yyyy-MM-dd HH:mm:ss")
            Level      = $levels[(Get-Random -Minimum 0 -Maximum 5)]
            Source     = $sources[(Get-Random -Minimum 0 -Maximum 5)]
            EventId    = Get-Random -Minimum 1000 -Maximum 9999
            Message    = $messages[(Get-Random -Minimum 0 -Maximum 8)]
        }
    }

    New-Item (Split-Path $OutputPath -Parent) -ItemType Directory -Force | Out-Null
    $events | Export-Csv $OutputPath -NoTypeInformation -Encoding utf8
    Write-Host "已建立測試資料：$OutputPath（$Count 筆）"
    return $OutputPath
}
```

### Step 2：主腳本框架

```powershell
# Invoke-LogAnalysis.ps1
[CmdletBinding()]
param(
    [ValidateScript({ Test-Path $_ -PathType Leaf })]
    [string]$LogPath,

    [string]$OutputPath = "C:\Temp\log-report.html",

    [ValidateRange(1, 90)]
    [int]$Since = 7
)
```

### Step 3：讀取與解析

注意 `Import-Csv` 讀進來的欄位都是字串，`Timestamp` 要轉成 `[datetime]` 才能做日期比較：

```powershell
$cutoff = (Get-Date).AddDays(-$Since)

$events = Import-Csv $LogPath -Encoding utf8 |
    ForEach-Object {
        # 把字串轉成強型別
        [PSCustomObject]@{
            Timestamp = [datetime]$_.Timestamp
            Level     = $_.Level
            Source    = $_.Source
            EventId   = [int]$_.EventId
            Message   = $_.Message
        }
    } |
    Where-Object { $_.Timestamp -ge $cutoff }
```

### Step 4：統計分析

```powershell
$total   = $events.Count
$byLevel = $events | Group-Object Level | Sort-Object Count -Descending
$bySource = $events | Group-Object Source | Sort-Object Count -Descending |
            Select-Object -First 5
$errors  = $events | Where-Object { $_.Level -eq "ERROR" } |
           Sort-Object Timestamp -Descending
```

### Step 5：生成 HTML 報告

```powershell
$levelRows = $byLevel | ForEach-Object {
    $pct = [Math]::Round($_.Count / $total * 100, 1)
    "<tr><td>$($_.Name)</td><td>$($_.Count)</td><td>${pct}%</td></tr>"
}

$errorRows = $errors | Select-Object -First 20 | ForEach-Object {
    "<tr><td>$($_.Timestamp)</td><td>$($_.Source)</td><td>$($_.Message)</td></tr>"
}

$html = @"
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>日誌分析報告</title>
<style>
  body { font-family: Arial; margin: 2em; }
  table { border-collapse: collapse; width: 100%; margin: 1em 0; }
  th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
  th { background: #4472C4; color: white; }
  tr:nth-child(even) { background: #f2f2f2; }
  .error { color: red; font-weight: bold; }
</style>
</head>
<body>
<h1>日誌分析報告</h1>
<p>分析區間：過去 $Since 天 | 截止：$(Get-Date -Format 'yyyy-MM-dd HH:mm') | 總計：$total 筆</p>

<h2>Level 統計</h2>
<table>
<tr><th>Level</th><th>數量</th><th>比例</th></tr>
$($levelRows -join "`n")
</table>

<h2>來源統計（前 5）</h2>
$($bySource | ConvertTo-Html -Fragment -Property Name, Count)

<h2>最近 20 筆 ERROR</h2>
<table>
<tr><th>時間</th><th>來源</th><th>訊息</th></tr>
$($errorRows -join "`n")
</table>
</body>
</html>
"@

$html | Out-File $OutputPath -Encoding utf8
```

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作</summary>

```powershell
# Invoke-LogAnalysis.ps1
[CmdletBinding()]
param(
    [string]$LogPath,
    [string]$OutputPath = "C:\Temp\log-report.html",
    [ValidateRange(1, 90)]
    [int]$Since = 7
)

$ErrorActionPreference = "Stop"

function New-TestLogData {
    param([int]$Count = 500, [string]$Path = "C:\Temp\test-events.csv")
    $levels  = @("INFO","INFO","INFO","WARN","ERROR")
    $sources = @("Application","System","Security","Service","Network")
    $msgs    = @("服務啟動","資料庫連線","記憶體警告","磁碟不足","認證失敗","網路逾時","任務完成","設定錯誤")
    $start   = (Get-Date).AddDays(-7)
    1..$Count | ForEach-Object {
        [PSCustomObject]@{
            Timestamp = $start.AddMinutes((Get-Random -Min 0 -Max 10080)).ToString("yyyy-MM-dd HH:mm:ss")
            Level     = $levels[(Get-Random -Min 0 -Max 5)]
            Source    = $sources[(Get-Random -Min 0 -Max 5)]
            EventId   = Get-Random -Min 1000 -Max 9999
            Message   = $msgs[(Get-Random -Min 0 -Max 8)]
        }
    } | Export-Csv $Path -NoTypeInformation -Encoding utf8
    Write-Host "測試資料已建立：$Path"
    $Path
}

# 沒給路徑就建測試資料
if (-not $LogPath) {
    $LogPath = New-TestLogData
}

try {
    if (-not (Test-Path $LogPath)) { throw "找不到日誌檔：$LogPath" }

    Write-Verbose "讀取：$LogPath"
    $cutoff = (Get-Date).AddDays(-$Since)

    $events = Import-Csv $LogPath -Encoding utf8 |
        ForEach-Object {
            [PSCustomObject]@{
                Timestamp = [datetime]$_.Timestamp
                Level     = $_.Level
                Source    = $_.Source
                EventId   = [int]$_.EventId
                Message   = $_.Message
            }
        } |
        Where-Object { $_.Timestamp -ge $cutoff }

    $total    = $events.Count
    $byLevel  = $events | Group-Object Level | Sort-Object Count -Descending
    $bySource = $events | Group-Object Source | Sort-Object Count -Descending | Select-Object -First 5
    $errors   = $events | Where-Object Level -eq "ERROR" | Sort-Object Timestamp -Descending

    # 摘要到 console
    Write-Host "`n=== 日誌分析報告 ===" -ForegroundColor Cyan
    Write-Host "區間：過去 $Since 天 | 總計：$total 筆"
    $byLevel | ForEach-Object {
        $pct = [Math]::Round($_.Count / $total * 100, 1)
        Write-Host ("  {0,-8}: {1,5} 筆 ({2}%)" -f $_.Name, $_.Count, $pct)
    }

    # 生成 HTML
    $levelRows  = $byLevel | ForEach-Object { $pct = [Math]::Round($_.Count/$total*100,1); "<tr><td>$($_.Name)</td><td>$($_.Count)</td><td>${pct}%</td></tr>" }
    $errorRows  = $errors | Select-Object -First 20 | ForEach-Object { "<tr><td>$($_.Timestamp)</td><td>$($_.Source)</td><td>$($_.Message)</td></tr>" }
    $sourceHtml = ($bySource | ForEach-Object { "<tr><td>$($_.Name)</td><td>$($_.Count)</td></tr>" }) -join "`n"

    $html = @"
<!DOCTYPE html><html><head><meta charset="utf-8"><title>日誌分析報告</title>
<style>body{font-family:Arial;margin:2em}table{border-collapse:collapse;width:100%;margin:1em 0}
th,td{border:1px solid #ccc;padding:8px}th{background:#4472C4;color:white}tr:nth-child(even){background:#f2f2f2}</style>
</head><body>
<h1>日誌分析報告</h1>
<p>過去 $Since 天 | 總計 $total 筆 | 產生時間：$(Get-Date -Format 'yyyy-MM-dd HH:mm')</p>
<h2>Level 統計</h2><table><tr><th>Level</th><th>數量</th><th>比例</th></tr>$($levelRows -join '')</table>
<h2>來源統計（前 5）</h2><table><tr><th>來源</th><th>數量</th></tr>$sourceHtml</table>
<h2>最近 20 筆 ERROR</h2><table><tr><th>時間</th><th>來源</th><th>訊息</th></tr>$($errorRows -join '')</table>
</body></html>
"@
    New-Item (Split-Path $OutputPath -Parent) -ItemType Directory -Force | Out-Null
    $html | Out-File $OutputPath -Encoding utf8
    Write-Host "`nHTML 報告：$OutputPath" -ForegroundColor Green

} catch {
    Write-Error "分析失敗：$($_.Exception.Message)"
    exit 1
}
```

</details>

## 測試用例

```powershell
# 1. 用預設測試資料
.\Invoke-LogAnalysis.ps1

# 2. 指定路徑和天數
.\Invoke-LogAnalysis.ps1 -LogPath C:\Temp\test-events.csv -Since 3

# 3. 測試錯誤處理
.\Invoke-LogAnalysis.ps1 -LogPath C:\不存在.csv

# 4. 開啟 HTML 報告
Start-Process "C:\Temp\log-report.html"
```

## 自我檢核

- [ ] `Import-Csv` 後有做型別轉換（Timestamp → datetime，EventId → int）
- [ ] 用了 `try/catch` 包住主邏輯
- [ ] HTML 報告能在瀏覽器正確顯示
- [ ] `-Since` 參數有做範圍驗證

→ [Ch 18 行程與服務](./18-process-and-service.md)
