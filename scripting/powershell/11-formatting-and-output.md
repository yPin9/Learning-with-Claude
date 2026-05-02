# Ch 11 — 格式化與輸出

> 目標：掌握 `Format-Table/List`、`Export-Csv`、`ConvertTo-Json`，以及理解「格式化 cmdlet 只能放在 pipeline 末端」的原則。

## 格式化 cmdlet 只能放末端

一個重要規則：`Format-*` 系列的輸出是**格式化物件**，不是原始物件。一旦套用 `Format-Table`，就不能再用 `Where-Object`、`Export-Csv` 等做後續處理：

```powershell
# 錯誤：Format-Table 後面接 Export-Csv 沒有意義
Get-Process | Format-Table | Export-Csv result.csv   # ← 會輸出格式字串，不是資料

# 正確：先處理資料，最後再格式化（給人看）或匯出（給機器用）
Get-Process | Export-Csv result.csv          # 匯出給機器
Get-Process | Format-Table -AutoSize         # 給人看
```

## Format-Table

```powershell
# 基本
Get-Process | Format-Table

# 指定欄位（包含計算屬性）
Get-Process |
    Format-Table Name, Id,
        @{N='MemMB'; E={ [int]($_.WorkingSet64 / 1MB) }; A='right'} -AutoSize

# -AutoSize：自動調整欄寬
# -Wrap：欄位太長時折行（不截斷）
# A = 對齊，'left' / 'right' / 'center'
```

## Format-List

每個物件的屬性垂直列出，適合看「單一物件的所有細節」：

```powershell
Get-Process notepad | Format-List *   # 所有屬性

# 或指定欄位
Get-Process notepad | Format-List Name, Id, CPU, StartTime
```

快速技巧：不確定要看什麼屬性時，先用 `Format-List *`；確定後再切回 `Format-Table`。

## Out-File 和 > 重導向

```powershell
Get-Process | Out-File C:\Temp\procs.txt
Get-Process > C:\Temp\procs.txt    # 同上，但 Out-File 能控制編碼

# 指定編碼（預設 UTF-16 LE，可能有 BOM 問題）
Get-Process | Out-File C:\Temp\procs.txt -Encoding utf8

# 附加到現有檔案
"新增一行" | Out-File C:\Temp\log.txt -Append
"新增一行" >> C:\Temp\log.txt   # >> 也是附加
```

## Export-Csv / Import-Csv

最常用的資料交換格式，Excel 和 Python 都能直接讀：

```powershell
# 匯出
Get-Process |
    Select-Object Name, Id, CPU, WorkingSet64 |
    Export-Csv C:\Temp\processes.csv -NoTypeInformation -Encoding utf8

# -NoTypeInformation：不加 #TYPE 開頭行（預設加，幾乎都不需要）

# 匯入
$data = Import-Csv C:\Temp\processes.csv
$data | Where-Object { [int]$_.Id -gt 1000 }

# 注意：Import-Csv 讀進來的所有值都是字串
# 需要比較數字時要轉型 [int]$_.Id
```

## ConvertTo-Json / ConvertFrom-Json

```powershell
# 物件轉 JSON
$servers = @(
    [PSCustomObject]@{ Name = "web01"; IP = "10.0.0.1" }
    [PSCustomObject]@{ Name = "db01";  IP = "10.0.0.2" }
)
$json = $servers | ConvertTo-Json
$json | Out-File servers.json -Encoding utf8

# JSON 轉物件
$loaded = Get-Content servers.json | ConvertFrom-Json
$loaded[0].Name   # web01

# 深度：預設 2 層，巢狀結構要加 -Depth
$complex = @{ A = @{ B = @{ C = "deep" } } }
$complex | ConvertTo-Json -Depth 5
```

## Export-CliXml / Import-CliXml

XML 格式，能完整序列化 .NET 物件（包含型別資訊），跨 session 傳遞複雜物件用它：

```powershell
Get-Process | Export-CliXml C:\Temp\procs.xml

$restored = Import-CliXml C:\Temp\procs.xml
$restored[0].GetType().Name   # Process（型別保留了）
```

## Write-Output vs Write-Host vs Write-Verbose

這三個很常混用，但行為不同：

| cmdlet | 進入 pipeline | 可被重導向 | 用途 |
|--------|-------------|-----------|------|
| `Write-Output` | 是 | 是 | 函式的「回傳值」 |
| `Write-Host`   | 否 | 否（直接到終端） | 顯示狀態訊息（不進管道） |
| `Write-Verbose`| 否 | 否 | 詳細偵錯訊息（`-Verbose` 才顯示） |
| `Write-Warning`| 否 | 是（stderr） | 警告 |
| `Write-Error`  | 否 | 是（stderr） | 錯誤 |

**函式裡的狀態訊息應用 `Write-Host` 或 `Write-Verbose`**，不能用 `Write-Output`（會進回傳值）。

## 動手練習

```powershell
# 1. 把前 10 個佔記憶體的行程匯出 CSV，再讀回來驗證
Get-Process |
    Sort-Object WorkingSet64 -Descending |
    Select-Object -First 10 Name, Id,
        @{N='MemMB'; E={ [int]($_.WorkingSet64 / 1MB) }} |
    Export-Csv C:\Temp\top10.csv -NoTypeInformation -Encoding utf8

$check = Import-Csv C:\Temp\top10.csv
$check | Format-Table
Write-Host "MemMB 最大：$( ($check | Sort-Object {[int]$_.MemMB} -Descending)[0].MemMB ) MB"

# 2. 試著在 Format-Table 後面接 Export-Csv，看看匯出的是什麼
```

## 自我檢核

- [ ] 理解 `Format-*` 只能放 pipeline 末端
- [ ] 知道 `Export-Csv` 要加 `-NoTypeInformation` 和 `-Encoding utf8`
- [ ] 理解 `Import-Csv` 讀進來的值都是字串，要比較數字需轉型
- [ ] 分清楚 `Write-Output`、`Write-Host`、`Write-Verbose` 的用途

→ [Ch 12 腳本參數化](./12-script-parameters.md)
