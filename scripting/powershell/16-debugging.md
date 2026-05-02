# Ch 16 — 偵錯技巧

> 目標：掌握 `-WhatIf`/`-Confirm`、`Write-Verbose`/`Write-Debug`、`Set-PSBreakpoint`、`Start-Transcript`，讓腳本問題無所遁形。

## -WhatIf：預覽模式

`-WhatIf` 是 PowerShell 最實用的安全機制，所有有副作用的 cmdlet 都支援：

```powershell
# 不執行，只顯示「如果跑了會做什麼」
Remove-Item C:\Temp\* -WhatIf
Stop-Process -Name notepad -WhatIf
Copy-Item C:\Source C:\Dest -Recurse -WhatIf

# 輸出範例：
# What if: Performing the operation "Remove File" on target "C:\Temp\old.log".
```

腳本部署前先加 `-WhatIf` 跑一遍，確認影響範圍。

## 讓自訂腳本支援 -WhatIf

加 `[CmdletBinding(SupportsShouldProcess)]`，然後包一層 `$PSCmdlet.ShouldProcess`：

```powershell
[CmdletBinding(SupportsShouldProcess)]
param([string]$Path)

Get-ChildItem $Path -File | ForEach-Object {
    if ($PSCmdlet.ShouldProcess($_.FullName, "刪除")) {
        Remove-Item $_.FullName
    }
}
```

現在你的腳本也支援 `-WhatIf` 和 `-Confirm` 了。

## Write-Verbose 和 Write-Debug

```powershell
[CmdletBinding()]
param([string]$Path)

Write-Verbose "開始掃描：$Path"   # 只有 -Verbose 時才顯示
Write-Debug "目前 Path 值：$Path" # 只有 -Debug 時才顯示

$files = Get-ChildItem $Path
Write-Verbose "找到 $($files.Count) 個檔案"

foreach ($f in $files) {
    Write-Debug "處理：$($f.Name)"
    # ...
}
```

```powershell
.\script.ps1 -Path C:\Logs                   # 靜默
.\script.ps1 -Path C:\Logs -Verbose          # 看 Verbose 訊息
.\script.ps1 -Path C:\Logs -Debug            # 看 Debug 訊息（還會暫停等確認）
```

習慣在腳本的關鍵步驟加 `Write-Verbose`，部署到生產後不需要刪，直接用 `-Verbose` 開關。

## $VerbosePreference 全局控制

```powershell
$VerbosePreference = "Continue"   # 全局開 Verbose，不用每次加 -Verbose
$DebugPreference   = "Continue"   # 全局開 Debug
```

## Set-PSBreakpoint：中斷點

在腳本裡設中斷點，執行到那行時暫停進入互動模式：

```powershell
# 在第 25 行設斷點
Set-PSBreakpoint -Script .\myscript.ps1 -Line 25

# 在變數被賦值時暫停
Set-PSBreakpoint -Script .\myscript.ps1 -Variable "count" -Mode Write

# 在函式被呼叫時暫停
Set-PSBreakpoint -Script .\myscript.ps1 -Command "Get-Data"

# 列出所有斷點
Get-PSBreakpoint

# 移除斷點
Remove-PSBreakpoint -Id 0
```

進入中斷點後，你在互動式 shell 裡，可以查變數、跑指令、然後輸入 `c`（continue）繼續或 `q`（quit）停止。

在 VSCode 裡，直接點行號左邊設紅點，按 F5 執行，有完整的 GUI 偵錯體驗。

## Trace-Command

追蹤 PS 內部的執行流程，用來診斷奇怪的行為：

```powershell
# 追蹤 pipeline 的參數綁定
Trace-Command -Name ParameterBinding -Expression {
    Get-Process | Where-Object CPU -gt 1
} -PSHost
```

這個比較進階，用來診斷「為什麼這個 cmdlet 不接受我給的值」很有用。

## Start-Transcript：記錄 Session

把整個 shell session 的輸入和輸出存成文字檔：

```powershell
Start-Transcript -Path C:\Temp\session.log -Append

# ... 做你要做的事 ...

Stop-Transcript
```

生產腳本的標準做法是在腳本開頭加 `Start-Transcript`，結束前加 `Stop-Transcript`，這樣有完整的執行記錄。

## 常見偵錯模式

```powershell
# 1. 印出中間值
$result = Get-SomeThing
Write-Host "DEBUG result = $($result | ConvertTo-Json)" -ForegroundColor Yellow

# 2. 在 pipeline 中間偷看
Get-Process |
    Where-Object CPU -gt 1 |
    Tee-Object -Variable temp |   # 把當前 pipeline 狀態存到 $temp
    Sort-Object CPU -Descending

# 3. 用 $DebugPreference 暫時開啟偵錯
$old = $DebugPreference
$DebugPreference = "Continue"
# ... 跑腳本 ...
$DebugPreference = $old
```

## 動手練習

```powershell
# 1. 建立一個有 Write-Verbose 的腳本，測試 -Verbose 開關
function Get-LargeFiles {
    [CmdletBinding()]
    param(
        [string]$Path = "C:\Windows",
        [int]$MinSizeMB = 10
    )

    Write-Verbose "掃描路徑：$Path，最小大小：${MinSizeMB}MB"
    $minBytes = $MinSizeMB * 1MB

    Get-ChildItem $Path -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            Write-Debug "檢查：$($_.Name) ($([int]($_.Length/1MB))MB)"
            $_.Length -gt $minBytes
        } |
        Sort-Object Length -Descending
}

# 先不加 -Verbose
Get-LargeFiles -Path C:\Windows\System32 -MinSizeMB 5 | Select-Object -First 3

# 再加 -Verbose
Get-LargeFiles -Path C:\Windows\System32 -MinSizeMB 5 -Verbose | Select-Object -First 3
```

## 自我檢核

- [ ] 習慣在刪除/修改操作前先用 `-WhatIf` 確認
- [ ] 知道讓自訂腳本支援 `-WhatIf` 需要 `[CmdletBinding(SupportsShouldProcess)]`
- [ ] 養成在腳本裡用 `Write-Verbose` 記錄進度的習慣
- [ ] 知道 `Start-Transcript` 可以記錄完整 session

→ [Ch 17 排程工作自動化](./17-scheduled-tasks.md)
