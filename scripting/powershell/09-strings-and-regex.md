# Ch 9 — 字串與正規表示式

> 目標：掌握 here-string、`-f` 格式化運算子、`-match`/`-replace`/`-split`，以及在 PowerShell 裡使用 regex 捕捉群組。

## 字串拼接

```powershell
# 1. + 號連接
$first = "Hello"
$last = "World"
$msg = $first + ", " + $last + "!"

# 2. 雙引號展開（推薦）
$msg = "$first, $last!"

# 3. -join（連接陣列）
$words = @("Hello", "World", "PowerShell")
$words -join ", "   # Hello, World, PowerShell
$words -join "-"    # Hello-World-PowerShell
```

## -f 格式化運算子

和 .NET 的 `String.Format` 一樣：

```powershell
"Hello, {0}! You are {1} years old." -f "Alice", 30
# Hello, Alice! You are 30 years old.

# 數字格式化
"{0:N2}" -f 1234567.891   # 1,234,567.89
"{0:P0}" -f 0.956          # 96%
"{0:D8}" -f 42             # 00000042（補零到 8 位）
"{0:X}" -f 255             # FF（十六進位）
```

## Here-String：多行字串

```powershell
# @" ... "@ 展開變數
$name = "Alice"
$text = @"
親愛的 $name，

這是一封信。
今天是 $(Get-Date -Format 'yyyy-MM-dd')。

祝好
"@

# @' ... '@ 原始字串（不展開）
$regex = @'
^(\d{4})-(\d{2})-(\d{2})$
'@
```

`@"` 和 `"@` **必須各自獨立一行**，`"@` 前面不能有空格。

## -split：分割字串

```powershell
"apple,banana,cherry" -split ","         # @("apple","banana","cherry")
"one  two   three"    -split "\s+"       # 按空白（多個）分割
"A1B2C3"              -split "(?<=\D)(?=\d)"  # 按字母和數字交界分割

# 限制分割次數
"a:b:c:d" -split ":", 2   # @("a", "b:c:d")
```

## -join：合併陣列

```powershell
@("2024", "01", "15") -join "-"   # 2024-01-15
@("a","b","c") -join ""           # abc
```

## -replace：取代

```powershell
"Hello World" -replace "World", "PowerShell"
# Hello PowerShell

# 支援 regex
"2024-01-15" -replace "(\d{4})-(\d{2})-(\d{2})", '$3/$2/$1'
# 15/01/2024  （$1 $2 $3 是捕捉群組）
```

注意：`-replace` 的替換字串裡的 `$1`、`$2` 是 regex 反向參考，**要用單引號**，否則 PS 會把 `$1` 當 PS 變數解析。

## -match：regex 比對

```powershell
"abc123" -match "\d+"   # True
$Matches[0]              # 123（整個比對）

# 捕捉群組
"2024-01-15" -match "^(?<year>\d{4})-(?<month>\d{2})-(?<day>\d{2})$"
$Matches['year']   # 2024
$Matches['month']  # 01
$Matches['day']    # 15
```

`-match` 把比對結果存在 `$Matches` 自動變數，命名捕捉群組用 `(?<name>...)` 語法。

## -notmatch、-cmatch

```powershell
"hello" -notmatch "\d"   # True（沒有數字）
"Hello" -cmatch "^h"     # False（區分大小寫，H ≠ h）
```

## 字串方法

```powershell
$s = "  Hello World  "
$s.Trim()           # "Hello World"     （移除前後空白）
$s.TrimStart()      # "Hello World  "
$s.TrimEnd()        # "  Hello World"
$s.ToLower()        # "  hello world  "
$s.ToUpper()        # "  HELLO WORLD  "
$s.Replace("o","0") # "  Hell0 W0rld  "
$s.StartsWith("  H")# True
$s.Contains("World")# True
$s.IndexOf("World") # 8
$s.Substring(7, 5)  # "World"  （從位置 7 取 5 個字元）
```

`Replace()` 方法區分大小寫，`-replace` 運算子預設不分。

## 動手練習

```powershell
# 1. 從日誌行裡提取 IP 地址
$logLine = "[2024-01-15 10:23:45] ERROR from 192.168.1.42: connection refused"
if ($logLine -match "(\d{1,3}\.){3}\d{1,3}") {
    "IP: $($Matches[0])"
}

# 2. 把 CamelCase 轉成 snake_case
$camel = "GetUserProfile"
$camel -creplace "(?<!^)([A-Z])", "_`$1" | ForEach-Object { $_.ToLower() }

# 3. 解析 CSV 行
$csv = "Alice,30,IT,台北"
$fields = $csv -split ","
"姓名:{0} 年齡:{1} 部門:{2} 地點:{3}" -f $fields[0], $fields[1], $fields[2], $fields[3]
```

## 自我檢核

- [ ] 理解單引號和雙引號在 here-string 裡的差異
- [ ] 能用 `-replace` 搭配捕捉群組做字串重排
- [ ] 能用 `-match` + `$Matches` 提取特定欄位
- [ ] 知道 `.Replace()` 方法和 `-replace` 運算子的大小寫行為差異

→ [Ch 10 陣列與 Hashtable 進階](./10-arrays-and-hashtables.md)
