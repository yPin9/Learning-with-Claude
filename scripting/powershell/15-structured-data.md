# Ch 15 — 文字與結構化資料

> 目標：熟練處理 CSV、JSON、XML 這三種最常見的資料格式，能在它們之間轉換，也能解析非結構化的文字日誌。

## CSV：最常見的交換格式

```powershell
# 讀取 CSV
$users = Import-Csv C:\Data\users.csv -Encoding utf8

$users[0]              # 第一筆
$users[0].Name         # 存取欄位
$users.Count           # 筆數

# 篩選和轉換
$users |
    Where-Object { $_.Department -eq "IT" } |
    Select-Object Name, Email

# 修改後匯出
$users |
    ForEach-Object {
        $_.Email = $_.Email.ToLower()
        $_   # 輸出物件，繼續 pipeline
    } |
    Export-Csv C:\Data\users_fixed.csv -NoTypeInformation -Encoding utf8
```

**提醒**：`Import-Csv` 讀進來的所有欄位都是 `[string]`，要做數值比較要先轉型：

```powershell
$users | Where-Object { [int]$_.Age -gt 30 }
```

## 建立 CSV 不從檔案讀

```powershell
# 用 PSCustomObject 陣列建立 CSV
@(
    [PSCustomObject]@{ Name = "Alice"; Dept = "IT";  Score = 95 }
    [PSCustomObject]@{ Name = "Bob";   Dept = "HR";  Score = 82 }
    [PSCustomObject]@{ Name = "Carol"; Dept = "IT";  Score = 91 }
) | Export-Csv C:\Data\scores.csv -NoTypeInformation -Encoding utf8
```

## JSON

```powershell
# 讀取 JSON 檔
$config = Get-Content C:\Config\settings.json -Raw | ConvertFrom-Json

$config.Database.Host    # 存取巢狀屬性
$config.Features[0]      # 存取陣列

# 修改後寫回
$config.Database.Port = 5433
$config | ConvertTo-Json -Depth 5 | Set-Content C:\Config\settings.json -Encoding utf8
```

深度很重要：`-Depth` 預設是 2，巢狀超過 2 層就會被截斷。不確定時加 `-Depth 10`。

```powershell
# 從 API 回應解析 JSON（下一個例子）
$response = Invoke-RestMethod "https://api.github.com/users/microsoft"
$response.public_repos   # 直接存取屬性
```

## XML

```powershell
# 讀 XML
[xml]$xml = Get-Content C:\Config\app.config -Encoding utf8

# 用 XPath 查詢
$xml.SelectNodes("//connectionStrings/add") |
    ForEach-Object { "$($_.name): $($_.connectionString)" }

# 直接存取節點
$xml.configuration.appSettings.add

# 修改 XML
$xml.configuration.appSettings.add |
    Where-Object { $_.key -eq "ApiUrl" } |
    ForEach-Object { $_.value = "https://new-api.example.com" }

$xml.Save("C:\Config\app.config")
```

## ConvertFrom-String（解析非結構化文字）

純文字日誌的解析通常靠 regex，配合 `-match` 或 `Select-String`：

```powershell
# 用 Select-String 篩選含關鍵字的行
$errors = Get-Content C:\Logs\app.log |
    Select-String -Pattern "ERROR|WARN" |
    Select-Object LineNumber, Line

# 用 regex 解析欄位
$logPattern = '^(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(?<level>\w+)\] (?<msg>.+)$'

Get-Content C:\Logs\app.log | ForEach-Object {
    if ($_ -match $logPattern) {
        [PSCustomObject]@{
            Timestamp = [datetime]$Matches['ts']
            Level     = $Matches['level']
            Message   = $Matches['msg']
        }
    }
} | Where-Object { $_.Level -eq "ERROR" } |
    Export-Csv C:\Logs\errors.csv -NoTypeInformation
```

## ConvertFrom-Csv（字串 CSV）

如果 CSV 資料不在檔案裡（例如從 pipeline 或 API 來的字串）：

```powershell
$csv = @"
Name,Age,City
Alice,30,Taipei
Bob,25,Kaohsiung
"@

$csv | ConvertFrom-Csv | ForEach-Object { "$($_.Name) 住在 $($_.City)" }
```

## 格式轉換

```powershell
# CSV → JSON
Import-Csv C:\Data\users.csv | ConvertTo-Json | Out-File C:\Data\users.json

# JSON → CSV
(Get-Content C:\Data\users.json | ConvertFrom-Json) |
    Export-Csv C:\Data\users_from_json.csv -NoTypeInformation

# 物件 → HTML 報告
Get-Process |
    Select-Object -First 10 Name, Id, CPU |
    ConvertTo-Html -Title "行程報告" -PreContent "<h1>Top 10 行程</h1>" |
    Out-File C:\Temp\report.html
```

## 動手練習

建立一個「日誌解析器」腳本：

```powershell
# 先建立一個模擬日誌檔
@"
2024-01-15 09:00:01 [INFO] 服務啟動
2024-01-15 09:00:15 [INFO] 資料庫連線成功
2024-01-15 09:05:32 [WARN] 記憶體用量超過 80%
2024-01-15 09:10:44 [ERROR] 連線逾時：db-server-01
2024-01-15 09:11:02 [INFO] 重試連線
2024-01-15 09:11:15 [ERROR] 認證失敗：user-api
"@ | Set-Content C:\Temp\test.log -Encoding utf8

# 解析日誌，統計各 Level 的數量
$pattern = '(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(?<level>\w+)\] (?<msg>.+)'

$parsed = Get-Content C:\Temp\test.log | ForEach-Object {
    if ($_ -match $pattern) {
        [PSCustomObject]@{
            Timestamp = [datetime]$Matches['ts']
            Level     = $Matches['level']
            Message   = $Matches['msg']
        }
    }
}

$parsed | Group-Object Level | Select-Object Name, Count
$parsed | Where-Object { $_.Level -eq "ERROR" }
```

## 自我檢核

- [ ] 知道 `Import-Csv` 讀進來的值都是字串，比較數字要轉型
- [ ] 知道 `ConvertTo-Json` 的 `-Depth` 預設只有 2
- [ ] 能用 regex + `PSCustomObject` 把非結構化日誌轉成物件
- [ ] 能做 CSV → JSON 和 JSON → CSV 的格式轉換

→ [Ch 16 偵錯技巧](./16-debugging.md)
