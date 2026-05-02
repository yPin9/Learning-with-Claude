# Ch 10 — 陣列與 Hashtable 進階

> 目標：掌握陣列的常見操作、`[ordered]` hashtable、巢狀結構，以及避免 `+=` 效能陷阱。

## 陣列基礎

```powershell
# 建立陣列
$arr = @(1, 2, 3, 4, 5)
$arr = 1, 2, 3, 4, 5   # @() 可以省略

# 存取
$arr[0]       # 1  （索引從 0 開始）
$arr[-1]      # 5  （最後一個）
$arr[1..3]    # @(2, 3, 4)  （slice）

# 長度
$arr.Count    # 5
$arr.Length   # 5  （一樣）
```

## 陣列操作

```powershell
# 加入元素（見下方效能陷阱說明）
$arr += 6      # @(1, 2, 3, 4, 5, 6)

# 連接兩個陣列
$a = @(1, 2, 3)
$b = @(4, 5, 6)
$c = $a + $b    # @(1, 2, 3, 4, 5, 6)

# 包含檢查
$arr -contains 3    # True

# 過濾（回傳符合的元素）
$arr | Where-Object { $_ -gt 3 }    # @(4, 5, 6)

# 排序
$arr | Sort-Object
$arr | Sort-Object -Descending

# 取唯一值
@(1, 2, 2, 3, 3, 3) | Select-Object -Unique   # @(1, 2, 3)
```

## += 的效能陷阱

PowerShell 的 `@()` 陣列是**固定大小**的 .NET 陣列，每次 `+=` 都會建立新陣列、複製所有元素——這是 O(n²)：

```powershell
# 效能差：每次 += 都重新建立整個陣列
$result = @()
foreach ($i in 1..10000) {
    $result += $i   # ← 10000 次重新建立陣列
}
```

**處理大量資料要用 `ArrayList` 或直接讓 pipeline 輸出**：

```powershell
# 方法 1：ArrayList（.Add() 是 O(1)）
$result = [System.Collections.ArrayList]::new()
foreach ($i in 1..10000) {
    [void]$result.Add($i)   # [void] 是為了不讓回傳值印出來
}

# 方法 2：Generic List（型別安全，效能更好）
$result = [System.Collections.Generic.List[int]]::new()
foreach ($i in 1..10000) {
    $result.Add($i)
}

# 方法 3：直接用 pipeline 輸出（最 PS 風格）
$result = 1..10000 | ForEach-Object { $_ * 2 }
```

小陣列（< 1000 個元素）差異不大，大資料集才有感。

## Hashtable

```powershell
$config = @{
    Server  = "web01"
    Port    = 443
    Timeout = 30
}

# 存取
$config["Server"]   # web01
$config.Server      # web01（.屬性語法）

# 新增 / 修改
$config["NewKey"] = "value"
$config.Port = 8443

# 刪除
$config.Remove("Timeout")

# 檢查 key 存在
$config.ContainsKey("Server")   # True

# 列出所有 key / value
$config.Keys
$config.Values
```

## [ordered] Hashtable

普通 Hashtable **不保證插入順序**（內部是 hash table，排列隨機）。如果順序重要，用 `[ordered]`：

```powershell
$h = [ordered]@{
    First  = 1
    Second = 2
    Third  = 3
}

$h.Keys   # First, Second, Third（保持插入順序）
```

## 巢狀結構

```powershell
$infra = @{
    "web01" = @{
        IP     = "192.168.1.10"
        Ports  = @(80, 443)
        Tags   = @("frontend", "production")
    }
    "db01" = @{
        IP    = "192.168.1.20"
        Ports = @(5432)
        Tags  = @("backend", "production")
    }
}

# 存取巢狀
$infra["web01"].IP            # 192.168.1.10
$infra["web01"].Ports[1]      # 443
$infra["web01"].Tags -contains "production"   # True
```

## Hashtable 轉 PSCustomObject

```powershell
$h = @{ Name = "Alice"; Age = 30 }
$obj = [PSCustomObject]$h

$obj.Name   # Alice（可以用 .屬性 存取）
$obj | Format-Table   # 漂亮的表格輸出
```

## 遍歷 Hashtable

```powershell
$config = @{ A = 1; B = 2; C = 3 }

# 用 GetEnumerator()
foreach ($kv in $config.GetEnumerator()) {
    "$($kv.Key) = $($kv.Value)"
}

# 用 Keys
foreach ($key in $config.Keys) {
    "$key = $($config[$key])"
}
```

## 動手練習

```powershell
# 1. 統計一段文字裡每個單字出現幾次
$text = "the quick brown fox jumps over the lazy dog the fox"
$wordCount = @{}
$text -split " " | ForEach-Object {
    if ($wordCount.ContainsKey($_)) {
        $wordCount[$_]++
    } else {
        $wordCount[$_] = 1
    }
}
$wordCount.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 5

# 2. 把 ArrayList 和陣列 += 的效能測一下
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$arr = @()
1..5000 | ForEach-Object { $arr += $_ }
$sw.Stop(); "陣列 +=: $($sw.ElapsedMilliseconds)ms"

$sw.Restart()
$list = [System.Collections.Generic.List[int]]::new()
1..5000 | ForEach-Object { $list.Add($_) }
$sw.Stop(); "Generic List: $($sw.ElapsedMilliseconds)ms"
```

## 自我檢核

- [ ] 理解 `+=` 對大陣列的 O(n²) 效能問題
- [ ] 知道 `[ordered]` 保持插入順序而普通 Hashtable 不保證
- [ ] 能遍歷 Hashtable 的 key-value 對
- [ ] 能把 Hashtable 轉成 PSCustomObject 用 `.屬性` 存取

→ [Ch 11 格式化與輸出](./11-formatting-and-output.md)
