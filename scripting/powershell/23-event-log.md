# Ch 23 — 事件日誌

> 目標：用 `Get-WinEvent` 查詢 Windows 事件日誌，掌握 `-FilterHashtable` 和 XPath 篩選，批次匯出分析。

## Get-WinEvent vs Get-EventLog

Windows 有兩種事件日誌：
- **傳統日誌**（System, Application, Security）：兩個 cmdlet 都能查
- **新式日誌**（Microsoft-Windows-*/Operational）：只有 `Get-WinEvent` 能查

**一律用 `Get-WinEvent`**，`Get-EventLog` 已在 PS 6+ 移除。

## 基本查詢

```powershell
# 列出所有可用的日誌
Get-WinEvent -ListLog * | Sort-Object LogName | Format-Table LogName, RecordCount

# 查特定日誌（預設最新 n 筆）
Get-WinEvent -LogName System -MaxEvents 20
Get-WinEvent -LogName Application -MaxEvents 50
Get-WinEvent -LogName Security -MaxEvents 10   # Security 需要管理員

# 事件的重要屬性
Get-WinEvent -LogName System -MaxEvents 5 | Format-List Id, LevelDisplayName, TimeCreated, ProviderName, Message
```

## -FilterHashtable：最快的過濾方式

在查詢源頭過濾，比 `Where-Object` 快很多（特別是大量事件時）：

```powershell
# 只查 Error 級別的事件
Get-WinEvent -FilterHashtable @{
    LogName   = 'System'
    Level     = 2           # 1=Critical, 2=Error, 3=Warning, 4=Info, 5=Verbose
}

# 時間範圍
Get-WinEvent -FilterHashtable @{
    LogName   = 'Application'
    StartTime = (Get-Date).AddHours(-24)   # 過去 24 小時
    EndTime   = Get-Date
}

# 特定 Event ID
Get-WinEvent -FilterHashtable @{
    LogName = 'Security'
    Id      = 4625          # 登入失敗
}

# 特定來源
Get-WinEvent -FilterHashtable @{
    LogName      = 'Application'
    ProviderName = 'Application Error'
}

# 組合條件
Get-WinEvent -FilterHashtable @{
    LogName   = 'System'
    Level     = @(1, 2)     # Critical 或 Error
    StartTime = (Get-Date).AddDays(-7)
}
```

Level 對照表：

| Level 值 | 名稱 |
|---------|------|
| 1 | Critical |
| 2 | Error |
| 3 | Warning |
| 4 | Information |
| 5 | Verbose |

## XPath 過濾

更精細的過濾，效能和 `-FilterHashtable` 差不多：

```powershell
# XPath 語法
$xPath = @"
*[System[
    Level<=2
    and TimeCreated[timediff(@SystemTime) <= 86400000]
]]
"@
# 86400000 毫秒 = 24 小時

Get-WinEvent -LogName System -FilterXPath $xPath

# 更複雜的 XPath（過濾 EventData 欄位）
$xPath = "*[System[EventID=4625] and EventData[Data[@Name='TargetUserName']='Administrator']]"
Get-WinEvent -LogName Security -FilterXPath $xPath
```

XPath 比 `-FilterHashtable` 更強大，但語法較複雜，通常用在需要過濾事件資料欄位的場景。

## 解析事件內容

```powershell
# Message 是完整的訊息文字
$events = Get-WinEvent -FilterHashtable @{ LogName = 'System'; Level = 2 } -MaxEvents 5
$events | ForEach-Object {
    Write-Host "[$($_.TimeCreated)] $($_.Id) $($_.ProviderName)" -ForegroundColor Red
    Write-Host $_.Message
    Write-Host "---"
}

# 取得結構化的事件屬性（XML 解析）
$event = Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = 4625 } -MaxEvents 1
[xml]$xml = $event.ToXml()
$xml.Event.EventData.Data | ForEach-Object {
    "$($_.Name): $('#text' | ForEach-Object { $_.'#text' } )"
}
```

## 常用的事件 ID

| 日誌 | Event ID | 意義 |
|------|---------|------|
| Security | 4624 | 登入成功 |
| Security | 4625 | 登入失敗 |
| Security | 4648 | 明確憑證登入 |
| Security | 4720 | 建立帳號 |
| Security | 4740 | 帳號鎖定 |
| System | 6005 | 事件日誌服務啟動（= 系統開機）|
| System | 6006 | 事件日誌服務停止（= 系統關機）|
| System | 41 | 非正常關機 |
| Application | 1000 | 應用程式錯誤 |

## 批次匯出分析

```powershell
# 過去 24 小時的所有錯誤，匯出 CSV
Get-WinEvent -FilterHashtable @{
    LogName   = @('System', 'Application')
    Level     = @(1, 2)
    StartTime = (Get-Date).AddHours(-24)
} |
    Select-Object TimeCreated, Id, LevelDisplayName, ProviderName,
        @{N='Message'; E={ $_.Message -replace "`r`n", " " }} |  # 單行化
    Export-Csv C:\Temp\errors.csv -NoTypeInformation -Encoding utf8

# 統計每個來源的錯誤數
Get-WinEvent -FilterHashtable @{
    LogName = 'Application'; Level = 2
    StartTime = (Get-Date).AddDays(-7)
} |
    Group-Object ProviderName |
    Sort-Object Count -Descending |
    Select-Object -First 10 Name, Count |
    Format-Table
```

## 監控：等待特定事件

```powershell
# 持續監控 Security 日誌，有新的登入失敗就即時通知
Register-WmiEvent -Class '__InstanceCreationEvent' `
    -Query "SELECT * FROM __InstanceCreationEvent WHERE TargetInstance ISA 'Win32_NTLogEvent' AND TargetInstance.EventCode=4625" `
    -Action {
        Write-Host "[警告] 登入失敗偵測到！" -ForegroundColor Red
    }
```

## 動手練習

```powershell
# 1. 查過去 7 天系統開機/關機記錄
Get-WinEvent -FilterHashtable @{
    LogName = 'System'
    Id      = @(6005, 6006, 41)     # 開機、關機、非正常關機
    StartTime = (Get-Date).AddDays(-7)
} |
    Select-Object TimeCreated, Id,
        @{N='Event'; E={
            switch ($_.Id) {
                6005 { "開機" }
                6006 { "關機" }
                41   { "非正常關機" }
            }
        }} |
    Sort-Object TimeCreated |
    Format-Table

# 2. 找出過去 24 小時出現最多次的錯誤來源
Get-WinEvent -FilterHashtable @{
    LogName = 'System', 'Application'
    Level = 2
    StartTime = (Get-Date).AddHours(-24)
} -ErrorAction SilentlyContinue |
    Group-Object ProviderName |
    Sort-Object Count -Descending |
    Select-Object -First 5 Name, Count
```

## 自我檢核

- [ ] 知道 `Get-WinEvent` 是 `Get-EventLog` 的繼承者
- [ ] 知道 `-FilterHashtable` 在查詢源頭過濾，比 `Where-Object` 快
- [ ] 能用 Level 數字過濾（2 = Error, 3 = Warning）
- [ ] 知道 Security 日誌 4625 是登入失敗、4624 是登入成功

→ [練習 B：系統健康報告腳本](./practice-b-health-report.md)
