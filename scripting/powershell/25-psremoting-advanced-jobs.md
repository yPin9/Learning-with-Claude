# Ch 25 — PSRemoting 進階與 Jobs

> 目標：掌握 PSSession 的建立與複用、Background Jobs 的完整生命週期，以及平行執行多個遠端工作的模式。

## PSSession：持久連線

每次 `Invoke-Command -ComputerName` 都會建立新連線，如果要對同一台機器做多次查詢，用 `New-PSSession` 保持連線：

```powershell
# 建立持久 Session
$session = New-PSSession -ComputerName "server01"
$session = New-PSSession -ComputerName "server01" -Credential $cred

# 用同一個 Session 執行多次
Invoke-Command -Session $session -ScriptBlock { Get-Service | Measure-Object | Select-Object Count }
Invoke-Command -Session $session -ScriptBlock { Get-Process | Measure-Object | Select-Object Count }

# Session 之間的狀態會保留！
Invoke-Command -Session $session -ScriptBlock { $x = 42 }
Invoke-Command -Session $session -ScriptBlock { "x = $x" }   # 輸出: x = 42
```

最後清理：

```powershell
Remove-PSSession $session
# 或
$session | Remove-PSSession
```

### 批次管理 Sessions

```powershell
$servers = @("web01", "web02", "db01")
$sessions = New-PSSession -ComputerName $servers

# 對所有 session 執行
Invoke-Command -Session $sessions -ScriptBlock {
    "$env:COMPUTERNAME: $(Get-Date)"
}

# 查看所有 session
Get-PSSession

# 關閉所有 session
Get-PSSession | Remove-PSSession
```

## Background Jobs：非同步執行

有些工作需要很長時間，不想等它跑完才繼續。`Start-Job` 讓工作在背景執行：

```powershell
# 在背景啟動工作
$job = Start-Job -ScriptBlock {
    Start-Sleep -Seconds 10
    "工作完成！時間：$(Get-Date)"
}

# 繼續做其他事...
Write-Host "工作已在背景執行（ID：$($job.Id)）"

# 等待完成（阻斷）
Wait-Job -Job $job
# 或等待並設 timeout
Wait-Job -Job $job -Timeout 30

# 取得結果
$result = Receive-Job -Job $job
Write-Host "結果：$result"

# 清理（完成的 job 需要手動移除）
Remove-Job -Job $job
```

### Jobs 生命週期

```powershell
# 查看所有 jobs
Get-Job

# 狀態：NotStarted, Running, Completed, Failed, Stopped

# 等待所有 job 完成
Get-Job | Wait-Job

# 取得所有已完成的結果
Get-Job | Where-Object State -eq Completed | Receive-Job

# 停止執行中的 job
Stop-Job -Id 3

# 清理所有已完成/失敗的 job
Get-Job | Remove-Job
```

## Invoke-Command -AsJob：遠端背景執行

結合 PSRemoting 和 Jobs：

```powershell
$servers = @("web01", "web02", "web03")

# 在多台機器同時執行，馬上回傳（不等結果）
$jobs = Invoke-Command -ComputerName $servers `
    -ScriptBlock { Invoke-WebRequest -Uri "https://internal-health-check" -UseBasicParsing } `
    -AsJob

# 做其他事...

# 等所有完成
$jobs | Wait-Job

# 取得結果
$results = $jobs | Receive-Job
$results | Select-Object PSComputerName, StatusCode
```

## 平行處理模式比較

PS 有三種平行方式，各有適用場景：

| 方式 | 適用場景 | 注意事項 |
|------|---------|---------|
| `ForEach-Object -Parallel` | 本機 CPU/IO bound 任務 | PS 7+，共享記憶體 |
| `Start-Job` | 需要獨立 Session / 長時間工作 | 有啟動開銷，不共享變數 |
| `Invoke-Command -AsJob` | 遠端多機器平行 | 需要 PSRemoting |

```powershell
# 方法 1：ForEach-Object -Parallel（PS 7，最快啟動）
$urls = @("server01", "server02", "server03")
$urls | ForEach-Object -Parallel {
    Test-Connection -ComputerName $_ -Count 1 -Quiet
} -ThrottleLimit 10

# 方法 2：Start-Job（適合獨立的大型工作）
$jobs = "server01", "server02" | ForEach-Object {
    $s = $_
    Start-Job -ScriptBlock {
        # 大型報告生成...
        "完成 $using:s"
    }
}
$jobs | Wait-Job | Receive-Job

# 方法 3：Invoke-Command -AsJob（遠端）
Invoke-Command -ComputerName "web01","web02" -ScriptBlock { ... } -AsJob
```

## Job 的錯誤處理

Jobs 的錯誤不會即時顯示，要用 `Receive-Job` 才看得到：

```powershell
$job = Start-Job -ScriptBlock {
    throw "Something went wrong"
}
Wait-Job $job

# 取得結果（失敗的 job 呼叫 Receive-Job 會拋出例外）
try {
    Receive-Job $job -ErrorAction Stop
} catch {
    "Job 失敗：$($_.Exception.Message)"
}

# 或先檢查狀態
if ($job.State -eq "Failed") {
    $job | Receive-Job -ErrorAction Continue
}

Remove-Job $job
```

## 範例：平行健康檢查

```powershell
function Invoke-ParallelHealthCheck {
    param([string[]]$ComputerName)

    $sessions = New-PSSession -ComputerName $ComputerName -ErrorAction SilentlyContinue

    $jobs = Invoke-Command -Session $sessions -ScriptBlock {
        $os     = Get-CimInstance Win32_OperatingSystem
        $disk   = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
        $diskPct = [Math]::Round(($disk.Size - $disk.FreeSpace) / $disk.Size * 100, 1)

        [PSCustomObject]@{
            Computer = $env:COMPUTERNAME
            Uptime   = [int]((Get-Date) - $os.LastBootUpTime).TotalHours
            DiskUsed = $diskPct
            MemFree  = [Math]::Round($os.FreePhysicalMemory / 1MB, 1)
            Status   = if ($diskPct -gt 90) { "CRITICAL" } elseif ($diskPct -gt 80) { "WARNING" } else { "OK" }
        }
    } -AsJob

    $jobs | Wait-Job | Out-Null
    $results = $jobs | Receive-Job
    $sessions | Remove-PSSession

    $results | Sort-Object Computer | Format-Table -AutoSize
}

Invoke-ParallelHealthCheck -ComputerName @("web01","web02","db01")
```

## 動手練習

```powershell
# 用 Start-Job 同時執行 3 個「工作」（模擬），測量總時間
$sw = [System.Diagnostics.Stopwatch]::StartNew()

$jobs = 1..3 | ForEach-Object {
    $n = $_
    Start-Job -ScriptBlock {
        Start-Sleep -Seconds 3   # 模擬 3 秒的工作
        "Job $using:n 完成 at $(Get-Date -Format HH:mm:ss)"
    }
}

$jobs | Wait-Job | Out-Null
$results = $jobs | Receive-Job
$sw.Stop()

$results
Write-Host "總耗時：$($sw.Elapsed.TotalSeconds) 秒（如果序列應該是 9 秒）"
Get-Job | Remove-Job
```

## 自我檢核

- [ ] 知道 `New-PSSession` 保持狀態（變數在 session 間共享）
- [ ] 理解 Job 的狀態機（NotStarted → Running → Completed/Failed）
- [ ] 知道 `Receive-Job` 之後結果就「消費掉了」（再呼叫沒有輸出）
- [ ] 能用 `Invoke-Command -AsJob` 對多台機器非同步執行

→ [Ch 26 Active Directory 模組](./26-active-directory.md)
