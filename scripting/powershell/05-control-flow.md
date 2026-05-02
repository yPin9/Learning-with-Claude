# Ch 5 — 流程控制

> 目標：掌握 if/switch/for/foreach/while 的語法，理解 `ForEach-Object`（pipeline）和 `foreach`（陳述式）的使用時機差異。

## if / elseif / else

```powershell
$score = 75

if ($score -ge 90) {
    "優秀"
} elseif ($score -ge 60) {
    "通過"
} else {
    "未通過"
}
```

條件**必須放在括號裡**。花括號可以換行，但左花括號要跟 if/elseif/else 同一行（PS 的 token 解析規則）：

```powershell
# 正確
if ($x -gt 0) {
    "正數"
}

# 錯誤：右花括號後面換行，else 和 } 不在同一行是 OK 的
# 但如果 } 和 else 中間換了行就會報錯
if ($x -gt 0) {
    "正數"
}
else {      # ← 這樣可以
    "非正數"
}
```

## switch

`switch` 比連串 `elseif` 更清楚，而且可以做萬用字元和 regex 比對：

```powershell
$day = "Monday"

switch ($day) {
    "Saturday" { "週末" }
    "Sunday"   { "週末" }
    default    { "工作日" }
}
```

多條件符合時，預設會**全部執行**（和 C 不同，不需要 break），除非你加 `break`：

```powershell
$n = 5

switch ($n) {
    { $_ -lt 10 }  { "小於 10" }   # 符合，執行
    { $_ -gt 3 }   { "大於 3" }    # 也符合，也執行
    # 兩行都會印出
}
```

`-Wildcard` 和 `-Regex` 模式：

```powershell
$filename = "report_2024.csv"

switch -Wildcard ($filename) {
    "*.csv" { "CSV 檔" }
    "*.xlsx" { "Excel 檔" }
    default { "其他" }
}

switch -Regex ($filename) {
    "^\d{4}" { "開頭是年份" }
    "_\d{4}\." { "包含年份" }
}
```

## for 迴圈

```powershell
for ($i = 0; $i -lt 5; $i++) {
    Write-Output "第 $i 次"
}
```

## foreach 陳述式

```powershell
$fruits = @("apple", "banana", "cherry")

foreach ($fruit in $fruits) {
    Write-Output "水果：$fruit"
}

# 也可以跑範圍
foreach ($i in 1..10) {
    Write-Output $i
}
```

## while 和 do-while

```powershell
$count = 0

while ($count -lt 3) {
    Write-Output "count = $count"
    $count++
}

# do-while：至少執行一次
do {
    $input = Read-Host "輸入 'quit' 離開"
} while ($input -ne "quit")
```

## break 和 continue

```powershell
foreach ($i in 1..10) {
    if ($i -eq 5) { continue }   # 跳過 5
    if ($i -eq 8) { break }      # 遇到 8 就停
    Write-Output $i
}
# 輸出: 1 2 3 4 6 7
```

## ForEach-Object（pipeline 版）

`foreach` 陳述式和 `ForEach-Object` cmdlet 功能類似，但使用情境不同：

```powershell
# foreach 陳述式：先把整個集合放進記憶體
$files = Get-ChildItem C:\Logs
foreach ($f in $files) {
    $f.Name
}

# ForEach-Object：流式處理，一次只處理一個物件（省記憶體）
Get-ChildItem C:\Logs | ForEach-Object { $_.Name }
```

處理大量資料（幾萬筆檔案、大型 CSV）時，`ForEach-Object` 在 pipeline 裡流式處理比較不吃記憶體。小資料集兩者差不多。

`ForEach-Object` 的 `-Begin`、`-Process`、`-End` 區塊：

```powershell
1..5 | ForEach-Object -Begin {
    $sum = 0
    "開始"
} -Process {
    $sum += $_
} -End {
    "總和：$sum"
}
```

PS 7 加入了平行 foreach：

```powershell
# -Parallel：多執行緒，適合 I/O bound 工作
1..10 | ForEach-Object -Parallel {
    Start-Sleep -Milliseconds 100
    "完成 $_"
} -ThrottleLimit 5  # 最多 5 個同時跑
```

## 動手練習

```powershell
# 1. 用 switch -Wildcard 判斷副檔名
$files = @("note.txt", "data.csv", "photo.jpg", "script.ps1")
foreach ($f in $files) {
    switch -Wildcard ($f) {
        "*.txt"  { Write-Output "$f: 文字檔" }
        "*.csv"  { Write-Output "$f: 資料檔" }
        "*.jpg"  { Write-Output "$f: 圖片" }
        "*.ps1"  { Write-Output "$f: PS 腳本" }
        default  { Write-Output "$f: 未知" }
    }
}

# 2. 用 while 找第一個大於 100 的 2 的次方
$n = 1
while ($n -le 100) { $n *= 2 }
Write-Output "第一個大於 100 的 2 的次方：$n"
```

## 自我檢核

- [ ] 知道 `switch` 預設多條件都執行，加 `break` 才跳出
- [ ] 理解 `foreach` 陳述式和 `ForEach-Object` 的使用時機差異
- [ ] 知道 PS 7 有 `-Parallel` 平行 foreach
- [ ] `do-while` 和 `while` 的差異（至少執行一次）

→ [Ch 6 函式與作用域](./06-functions-and-scope.md)
