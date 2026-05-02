# Ch 24 — PSRemoting 基礎

> 目標：啟用 WinRM、建立遠端 session、用 `Invoke-Command` 在遠端機器執行指令，理解互動式和非互動式兩種遠端模式。

## PSRemoting 的底層：WinRM

PSRemoting 建在 **WinRM**（Windows Remote Management）上，使用 HTTP(S) 傳輸，預設 Port 5985（HTTP）或 5986（HTTPS）。

```
本機 PS → [WinRM 5985/5986] → 遠端 WSMan → 遠端 PS
```

## 啟用 PSRemoting

在**目標機器**上以管理員身分執行：

```powershell
Enable-PSRemoting -Force

# 這個指令做了幾件事：
# 1. 啟動 WinRM 服務並設為自動啟動
# 2. 設定 WinRM Listener（監聽 HTTP 5985）
# 3. 設定防火牆規則允許 WinRM 流量
```

確認設定：

```powershell
Get-WSManInstance -ResourceURI winrm/config
winrm quickconfig   # 傳統方式確認
```

## 互動式遠端：Enter-PSSession

像 SSH 一樣進入遠端機器的 shell：

```powershell
# 連線（使用目前的憑證）
Enter-PSSession -ComputerName "server01"

# 指定帳號
Enter-PSSession -ComputerName "server01" -Credential "DOMAIN\admin"

# 連線後 prompt 會變成：
# [server01]: PS C:\>

# 離開
Exit-PSSession
```

`Enter-PSSession` 是互動式的，適合手動操作，但不適合腳本（因為整個 session 在遠端）。

## 非互動式遠端：Invoke-Command

在遠端機器執行一段程式碼，然後把結果傳回來——這才是腳本裡應該用的：

```powershell
# 執行單一指令
Invoke-Command -ComputerName "server01" -ScriptBlock {
    Get-Service | Where-Object Status -eq Running | Measure-Object | Select-Object -ExpandProperty Count
}

# 執行腳本檔（在本機讀腳本，在遠端執行）
Invoke-Command -ComputerName "server01" -FilePath "C:\Scripts\check.ps1"

# 傳入本機變數（用 $using:）
$threshold = 10
Invoke-Command -ComputerName "server01" -ScriptBlock {
    Get-Process | Where-Object { $_.CPU -gt $using:threshold }
}
```

## 多台機器平行執行

`Invoke-Command` 預設**同時對所有機器平行執行**，不是一台一台排隊：

```powershell
$servers = @("web01", "web02", "web03", "db01")

$results = Invoke-Command -ComputerName $servers -ScriptBlock {
    [PSCustomObject]@{
        Computer = $env:COMPUTERNAME
        OS       = (Get-CimInstance Win32_OperatingSystem).Caption
        Uptime   = [int]((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalHours
    }
}

$results | Sort-Object Computer | Format-Table
```

結果物件會有 `PSComputerName` 屬性記錄是哪台機器的資料。

## 憑證管理

```powershell
# 輸入一次密碼，存成物件，複用
$cred = Get-Credential -UserName "DOMAIN\admin" -Message "輸入管理員密碼"

Invoke-Command -ComputerName "server01" -Credential $cred -ScriptBlock { hostname }
Enter-PSSession -ComputerName "server01" -Credential $cred
```

## 跨域或工作群組的額外設定

預設 PSRemoting 只接受網域機器。連到工作群組機器或不同網域時，要做額外設定：

```powershell
# 在來源機器上，把目標加入受信任主機清單
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "server01,192.168.1.50" -Force
# 或加入所有（不推薦生產環境）
Set-Item WSMan:\localhost\Client\TrustedHosts -Value "*" -Force

# 確認
Get-Item WSMan:\localhost\Client\TrustedHosts
```

## 常見問題診斷

```powershell
# 測試 WinRM 連線
Test-WSMan -ComputerName "server01"

# 詳細連線資訊
Test-WSMan -ComputerName "server01" -Credential $cred

# 如果 Test-NetConnection 通但 PSRemoting 不通，可能是：
Test-NetConnection -ComputerName "server01" -Port 5985
# 1. WinRM 未啟用（在目標跑 Enable-PSRemoting）
# 2. 防火牆擋住 5985 Port
# 3. 跨 subnet 未加 TrustedHosts
```

## Invoke-Command 的輸出

遠端回傳的物件是序列化的（Deserialized），很多方法沒辦法直接呼叫，但屬性都還在：

```powershell
$proc = Invoke-Command -ComputerName "server01" -ScriptBlock { Get-Process -Name "svchost" | Select-Object -First 1 }

# 屬性可以存取
$proc.Name
$proc.Id

# 型別名稱前面會加 Deserialized.
$proc.GetType().FullName  # Deserialized.System.Diagnostics.Process

# 方法通常不能用（序列化物件失去方法）
# $proc.Kill()  ← 這個不能用，要 Invoke-Command 到遠端才能呼叫
```

## 動手練習

如果你有兩台機器，試以下操作。沒有的話，用 `localhost` 練習：

```powershell
# 1. 確認本機 PSRemoting 可以自連
Invoke-Command -ComputerName "localhost" -ScriptBlock { "Hello from $(hostname)" }

# 2. 取得本機的前 5 個最耗記憶體的行程（via PSRemoting）
Invoke-Command -ComputerName "localhost" -ScriptBlock {
    Get-Process |
        Sort-Object WorkingSet64 -Descending |
        Select-Object -First 5 Name, Id,
            @{N='MemMB'; E={ [int]($_.WorkingSet64/1MB) }}
} | Format-Table
```

## 自我檢核

- [ ] 知道 `Enable-PSRemoting` 要在目標機器上跑
- [ ] 理解 `Enter-PSSession`（互動）和 `Invoke-Command`（腳本）的使用時機差異
- [ ] 知道 `$using:變數` 是在遠端 ScriptBlock 裡取得本機變數的方式
- [ ] 理解遠端回傳的物件是 Deserialized，方法不可用

→ [Ch 25 PSRemoting 進階與 Jobs](./25-psremoting-advanced-jobs.md)
