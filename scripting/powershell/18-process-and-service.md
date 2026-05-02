# Ch 18 — 行程與服務

> 目標：用 PowerShell 管理 Windows 行程和服務，取代工作管理員和 services.msc。

## 行程管理

### 查詢行程

```powershell
# 列出所有行程
Get-Process

# 按名稱篩選（支援萬用字元）
Get-Process -Name "chrome"
Get-Process -Name "chrome*"

# 按 ID
Get-Process -Id 1234

# 行程屬性一覽
Get-Process | Get-Member -MemberType Property | Select-Object Name

# 實用的欄位
Get-Process | Select-Object Name, Id, CPU,
    @{N='MemMB'; E={ [int]($_.WorkingSet64 / 1MB) }},
    StartTime, MainWindowTitle |
    Sort-Object MemMB -Descending |
    Format-Table -AutoSize
```

### 啟動行程

```powershell
# 啟動應用程式
Start-Process notepad.exe
Start-Process "C:\Program Files\App\app.exe"

# 以參數啟動
Start-Process pwsh.exe -ArgumentList "-File C:\Scripts\run.ps1"

# 以管理員身分執行
Start-Process pwsh.exe -Verb RunAs

# 等待執行完畢才繼續（同步）
Start-Process notepad.exe -Wait

# 不顯示視窗（背景執行）
Start-Process pwsh.exe -ArgumentList "-File C:\Scripts\batch.ps1" -WindowStyle Hidden
```

### 停止行程

```powershell
# 按名稱停止
Stop-Process -Name "notepad"

# 按 ID 停止
Stop-Process -Id 1234

# 強制終止
Stop-Process -Name "chrome" -Force

# 停止所有同名行程
Get-Process -Name "chrome" | Stop-Process -Force

# 停止並確認
Stop-Process -Name "notepad" -Confirm

# -WhatIf 預覽
Stop-Process -Name "notepad" -WhatIf
```

### 等待行程結束

```powershell
$proc = Start-Process "robocopy.exe" -ArgumentList "C:\Src C:\Dst /E" -PassThru
$proc.WaitForExit()
Write-Host "結束代碼：$($proc.ExitCode)"
```

## 服務管理

### 查詢服務

```powershell
# 列出所有服務
Get-Service

# 按名稱
Get-Service -Name "wuauserv"     # Windows Update
Get-Service -Name "w32*"         # 萬用字元

# 按狀態篩選
Get-Service | Where-Object Status -eq Running
Get-Service | Where-Object Status -eq Stopped

# 查依賴關係
Get-Service -Name "netlogon" -DependentServices     # 哪些服務依賴它
Get-Service -Name "netlogon" -RequiredServices      # 它依賴哪些服務
```

### 啟動 / 停止 / 重啟

```powershell
# 啟動
Start-Service -Name "wuauserv"

# 停止
Stop-Service -Name "wuauserv"

# 強制停止（包括依賴它的服務）
Stop-Service -Name "wuauserv" -Force

# 重啟（等同 Stop + Start）
Restart-Service -Name "wuauserv"

# 暫停 / 恢復（需要服務支援）
Suspend-Service -Name "servicename"
Resume-Service  -Name "servicename"
```

### 修改服務設定

```powershell
# 設定啟動類型
Set-Service -Name "wuauserv" -StartupType Disabled    # 停用
Set-Service -Name "wuauserv" -StartupType Automatic   # 自動
Set-Service -Name "wuauserv" -StartupType Manual      # 手動

# 修改顯示名稱和描述
Set-Service -Name "myservice" -DisplayName "My Service" -Description "說明文字"
```

### 安裝和移除服務

```powershell
# 建立新服務（New-Service）
New-Service -Name "MyDaemon" `
    -BinaryPathName "C:\Apps\daemon.exe" `
    -DisplayName "My Background Daemon" `
    -StartupType Automatic `
    -Description "執行背景工作"

# 移除服務（兩種方式）
# 方法 1：sc.exe（系統工具）
sc.exe delete MyDaemon

# 方法 2：WMI（後面會講）
(Get-WmiObject Win32_Service -Filter "Name='MyDaemon'").Delete()
```

## 監控行程和服務的變化

```powershell
# 每 5 秒檢查服務狀態
while ($true) {
    $status = (Get-Service -Name "wuauserv").Status
    Write-Host "$(Get-Date -Format HH:mm:ss) - wuauserv: $status"
    Start-Sleep -Seconds 5
}

# 等待服務到達特定狀態（最多等 60 秒）
function Wait-ServiceStatus {
    param(
        [string]$Name,
        [string]$Status,
        [int]$TimeoutSeconds = 60
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if ((Get-Service $Name).Status -eq $Status) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

Restart-Service -Name "wuauserv"
if (Wait-ServiceStatus -Name "wuauserv" -Status "Running") {
    "服務已啟動"
} else {
    "逾時，服務未啟動"
}
```

## 動手練習

```powershell
# 1. 找出記憶體超過 50MB 且有視窗標題的行程
Get-Process |
    Where-Object { $_.WorkingSet64 -gt 50MB -and $_.MainWindowTitle } |
    Select-Object Name, Id,
        @{N='MemMB'; E={ [int]($_.WorkingSet64/1MB) }},
        MainWindowTitle |
    Sort-Object MemMB -Descending |
    Format-Table -AutoSize

# 2. 找出所有「自動啟動但目前沒在跑」的服務（可能是服務崩潰了）
Get-Service |
    Where-Object { $_.StartType -eq "Automatic" -and $_.Status -eq "Stopped" } |
    Select-Object Name, DisplayName |
    Format-Table -AutoSize
```

## 自我檢核

- [ ] 能用 `Get-Process` + `Where-Object` 找特定行程
- [ ] 知道 `Start-Process -PassThru` 可以取得行程物件再 `.WaitForExit()`
- [ ] 能找出「自動啟動但已停止」的異常服務
- [ ] 知道 `Stop-Service -Force` 會連依賴的服務一起停

→ [Ch 19 本機使用者與群組](./19-local-users-and-groups.md)
