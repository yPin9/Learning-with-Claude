# Ch 17 — 排程工作自動化

> 目標：用 PowerShell 建立、修改、管理 Windows 排程工作，取代手動點 Task Scheduler GUI。

## 為什麼不用 GUI

Task Scheduler GUI 很難版本控制，也沒辦法批次建立。用 PowerShell 腳本建立排程工作，好處：
- 可以 git 追蹤
- 可以用同一份腳本部署到 100 台機器
- 方便修改和測試

## 建立排程工作的四個元件

```
Action    ← 要執行什麼
Trigger   ← 什麼時候執行
Principal ← 以誰的身分執行
Settings  ← 執行條件（超時、重試等）
```

## 建立第一個排程工作

```powershell
# 要跑的指令
$action = New-ScheduledTaskAction `
    -Execute "pwsh.exe" `
    -Argument '-NonInteractive -File "C:\Scripts\backup.ps1"'

# 每天早上 2:00 觸發
$trigger = New-ScheduledTaskTrigger -Daily -At "02:00"

# 以 SYSTEM 帳號執行（不需要密碼，有最高權限）
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -RunLevel Highest

# 設定：最多跑 1 小時，沒插電也跑
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RunOnlyIfIdle:$false

# 整合成排程工作並註冊
Register-ScheduledTask `
    -TaskName "DailyBackup" `
    -TaskPath "\MyScripts\" `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "每日備份腳本"
```

## 觸發器類型

```powershell
# 每天
New-ScheduledTaskTrigger -Daily -At "03:00"

# 每週（週一和週三）
New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Wednesday -At "09:00"

# 開機時
New-ScheduledTaskTrigger -AtStartup

# 登入時
New-ScheduledTaskTrigger -AtLogon

# 一次性
New-ScheduledTaskTrigger -Once -At "2024-12-31 23:59"

# 每 15 分鐘（組合）
$t = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Hours 24) `
    -Once -At (Get-Date)
```

## 以特定使用者帳號執行

```powershell
# 以特定帳號執行（需要密碼）
$principal = New-ScheduledTaskPrincipal `
    -UserId "DOMAIN\svc_backup" `
    -LogonType Password

# 或：以現在登入的使用者執行
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive
```

## 管理現有排程工作

```powershell
# 列出所有
Get-ScheduledTask

# 按名稱或路徑篩選
Get-ScheduledTask -TaskName "DailyBackup"
Get-ScheduledTask -TaskPath "\MyScripts\*"

# 手動執行
Start-ScheduledTask -TaskName "DailyBackup"

# 停用 / 啟用
Disable-ScheduledTask -TaskName "DailyBackup"
Enable-ScheduledTask -TaskName "DailyBackup"

# 取得上次執行結果
$task = Get-ScheduledTask -TaskName "DailyBackup"
$task | Get-ScheduledTaskInfo
# LastRunTime, LastTaskResult (0 = 成功), NextRunTime

# 修改觸發器
$task = Get-ScheduledTask -TaskName "DailyBackup"
$task.Triggers[0].StartBoundary = "2024-02-01T03:00:00"
$task | Set-ScheduledTask

# 刪除
Unregister-ScheduledTask -TaskName "DailyBackup" -Confirm:$false
```

## 常見腳本模板

讓排程工作的腳本本身記錄執行結果：

```powershell
# C:\Scripts\backup.ps1
$logFile = "C:\Logs\backup_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
Start-Transcript -Path $logFile

try {
    Write-Host "備份開始：$(Get-Date)"

    # ... 備份邏輯 ...

    Write-Host "備份完成：$(Get-Date)"
    exit 0
} catch {
    Write-Error "備份失敗：$($_.Exception.Message)"
    exit 1
} finally {
    Stop-Transcript
}
```

排程工作的 `LastTaskResult` 會記錄 exit code，`0` 是成功，非 `0` 是失敗。

## 批次部署到多台機器

```powershell
$computers = @("server01", "server02", "server03")

Invoke-Command -ComputerName $computers {
    $action    = New-ScheduledTaskAction -Execute "pwsh.exe" `
                     -Argument '-File "C:\Scripts\health-check.ps1"'
    $trigger   = New-ScheduledTaskTrigger -Daily -At "06:00"
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
    Register-ScheduledTask -TaskName "HealthCheck" -Action $action `
        -Trigger $trigger -Principal $principal -Force
}
```

## 動手練習

```powershell
# 1. 建立一個測試用的排程工作，每分鐘把時間寫入一個文字檔
$action = New-ScheduledTaskAction `
    -Execute "pwsh.exe" `
    -Argument "-Command `"Add-Content C:\Temp\ticker.txt -Value (Get-Date).ToString()`""

$trigger = New-ScheduledTaskTrigger `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Hours 1) `
    -Once -At (Get-Date)

Register-ScheduledTask -TaskName "TestTicker" -Action $action -Trigger $trigger `
    -RunLevel Highest -Force

# 等幾分鐘後查看
Get-Content C:\Temp\ticker.txt

# 清理
Unregister-ScheduledTask -TaskName "TestTicker" -Confirm:$false
```

## 自我檢核

- [ ] 能建立 Action / Trigger / Principal / Settings 四個元件並整合
- [ ] 知道 SYSTEM 帳號不需要密碼，適合背景工作
- [ ] 能用 `Get-ScheduledTaskInfo` 查上次執行結果（LastTaskResult = 0 是成功）
- [ ] 能用 `Invoke-Command` 批次部署排程工作到多台機器

→ [練習 A：日誌分析腳本](./practice-a-log-analysis.md)
