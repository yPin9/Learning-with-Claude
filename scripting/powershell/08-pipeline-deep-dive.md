# Ch 8 — Pipeline 深入

> 目標：熟練使用 `Where-Object`、`Select-Object`、`Sort-Object`、`Group-Object`、`Measure-Object`，能串出複雜的資料處理 pipeline。

## Where-Object：過濾

最常用的 pipeline cmdlet，過濾不符合條件的物件：

```powershell
# 完整語法
Get-Service | Where-Object { $_.Status -eq 'Running' }

# 簡化語法（PS 3.0+）：單一比較可以省略 scriptblock
Get-Service | Where-Object Status -eq 'Running'
Get-Process | Where-Object CPU -gt 5

# ? 是別名
Get-Service | ? { $_.Status -eq 'Running' }
```

複合條件只能用 scriptblock 語法：

```powershell
Get-Process | Where-Object { $_.CPU -gt 1 -and $_.Name -like "chrome*" }
```

## Select-Object：選欄位 + 切片

```powershell
# 選特定欄位
Get-Process | Select-Object Name, Id, CPU

# 選前/後 N 筆
Get-Process | Select-Object -First 5
Get-Process | Select-Object -Last 3
Get-Process | Select-Object -Skip 10 -First 5   # 跳過 10 筆後取 5 筆

# 展開單一屬性成純值（而不是物件）
Get-Process | Select-Object -ExpandProperty Name

# 計算屬性
Get-Process | Select-Object Name, @{N='MemMB'; E={ [int]($_.WorkingSet64 / 1MB) }}
```

`-ExpandProperty` 很常用，它把物件的某個屬性「解包」成純值，讓你能繼續管道操作：

```powershell
# 不加 -ExpandProperty：輸出的是有一個欄位的物件
Get-Process | Select-Object Name | Get-Member   # TypeName: Selected.System.Diagnostics.Process

# 加 -ExpandProperty：輸出的是純字串
Get-Process | Select-Object -ExpandProperty Name | Get-Member   # TypeName: System.String
```

## Sort-Object：排序

```powershell
# 升序（預設）
Get-Process | Sort-Object CPU

# 降序
Get-Process | Sort-Object CPU -Descending

# 多欄位排序
Get-Process | Sort-Object Company, Name

# 不分大小寫的字串排序（-CaseSensitive 反之）
Get-ChildItem | Sort-Object Name
```

## Group-Object：分組

把物件按某個屬性分組，很像 SQL 的 GROUP BY：

```powershell
Get-Service | Group-Object Status

# 輸出：
# Count Name    Group
# ----- ----    -----
#   152 Running {...}
#    68 Stopped {...}

# 分組後取每組的物件
Get-Service |
    Group-Object Status |
    ForEach-Object {
        "$($_.Name)：$($_.Count) 個服務"
    }

# 只要計數
Get-EventLog -LogName Application -Newest 1000 |
    Group-Object Source |
    Sort-Object Count -Descending |
    Select-Object -First 10 Name, Count
```

## Measure-Object：統計

對數值屬性做統計：

```powershell
# 計算行程記憶體的統計數字
Get-Process | Measure-Object WorkingSet64 -Sum -Average -Maximum -Minimum

# 計算文字行數
Get-Content C:\logs\app.log | Measure-Object -Line

# 計算字元數和字數
"Hello World PowerShell" | Measure-Object -Word -Character
```

## ForEach-Object：轉換

最靈活的 pipeline 工具，對每個物件執行任意操作：

```powershell
# 一般用法
1..5 | ForEach-Object { $_ * 2 }   # 2 4 6 8 10

# % 是別名
1..5 | % { $_ * 2 }

# 呼叫方法的簡化語法（PS 3.0+）
"hello","world" | ForEach-Object ToUpper   # HELLO WORLD

# 存取屬性的簡化語法
Get-Process | ForEach-Object Name   # 等同 Select-Object -ExpandProperty Name
```

## Tee-Object：分叉輸出

把 pipeline 同時送到螢幕和檔案：

```powershell
Get-Process | Tee-Object -FilePath C:\Temp\procs.txt | Select-Object -First 5
```

## 完整範例

找出系統裡記憶體超過 100MB 的行程，按記憶體排序，格式化後輸出：

```powershell
Get-Process |
    Where-Object { $_.WorkingSet64 -gt 100MB } |
    Sort-Object WorkingSet64 -Descending |
    Select-Object Name, Id,
        @{N='MemMB'; E={ [Math]::Round($_.WorkingSet64 / 1MB, 1) }},
        @{N='CPU(s)'; E={ [Math]::Round($_.CPU, 2) }} |
    Format-Table -AutoSize
```

## 動手練習

```powershell
# 1. 找出所有 Stopped 的服務，按名稱排序，只取前 10 個
Get-Service |
    Where-Object Status -eq Stopped |
    Sort-Object Name |
    Select-Object -First 10 Name, DisplayName

# 2. 統計每個公司的行程數量
Get-Process |
    Where-Object { $_.Company } |
    Group-Object Company |
    Sort-Object Count -Descending |
    Select-Object -First 5 Name, Count

# 3. 計算 C:\Windows\System32 裡所有 .dll 的總大小
Get-ChildItem C:\Windows\System32 -Filter *.dll |
    Measure-Object Length -Sum |
    Select-Object @{N='TotalMB'; E={ [Math]::Round($_.Sum / 1MB, 1) }}
```

## 自我檢核

- [ ] 理解 `Select-Object -ExpandProperty` 和不加時的差異
- [ ] 能用 `Group-Object` + `Sort-Object` 做頻率統計
- [ ] 能寫包含計算屬性 `@{N=...; E={...}}` 的 pipeline
- [ ] 理解 `Measure-Object` 有 `-Sum`, `-Average`, `-Line` 等參數

→ [Ch 9 字串與正規表示式](./09-strings-and-regex.md)
