# Ch 3 — 變數與資料型別

> 目標：掌握 PowerShell 的變數宣告、常用資料型別、型別標注，以及 `$null` 的正確使用方式。

## 變數基礎

PowerShell 變數以 `$` 開頭，不需要宣告直接賦值：

```powershell
$name = "Alice"
$age  = 30
$isAdmin = $true

Write-Output $name    # Alice
Write-Output $age     # 30
```

變數名稱大小寫不敏感（`$Name` 和 `$name` 是同一個），但慣例用 camelCase 或 PascalCase。

## 常用資料型別

PowerShell 會自動推斷型別，但你可以明確標注：

```powershell
$s  = "hello"              # [string]
$i  = 42                   # [int]
$d  = 3.14                 # [double]
$b  = $true                # [bool]  ($true / $false)
$dt = Get-Date             # [datetime]
$n  = $null                # null 值
```

查變數的型別：

```powershell
$i.GetType()
# IsPublic IsSerial Name    BaseType
# -------- -------- ----    --------
# True     True     Int32   System.ValueType

$i.GetType().Name   # Int32
```

## 型別標注

加上 `[型別]` 限制變數只能存某種型別：

```powershell
[int]$count = 10
$count = "hello"   # 會自動嘗試轉型，轉不了就報錯

[string]$msg = 42   # 42 會被轉成 "42"，沒有報錯
```

型別標注最重要的用途是**函式參數**，後面 Ch 12 會講。在一般腳本裡，可以視情況加（讓意圖清楚）或不加（省事）。

## 字串

單引號 `'` — 原始字串，**不展開**變數和跳脫序列：

```powershell
$name = "Alice"
'Hello, $name'    # 輸出: Hello, $name  （不展開）
'C:\n\test'       # 輸出: C:\n\test     （\n 不是換行）
```

雙引號 `"` — **展開**變數：

```powershell
"Hello, $name"        # 輸出: Hello, Alice
"2 + 2 = $(2 + 2)"   # 輸出: 2 + 2 = 4  （$() 可執行表達式）
```

規則很簡單：要展開變數用雙引號，不想展開用單引號（通常用在路徑、regex）。

## 數字

```powershell
$a = 10
$b = 3

$a + $b    # 13
$a - $b    # 7
$a * $b    # 30
$a / $b    # 3.3333...（double）
$a % $b    # 1（取餘數）
[int]($a / $b)   # 3（截斷）
[math]::Round($a / $b, 2)   # 3.33（四捨五入到小數第2位）
```

大數字可以用底線分隔（PS 7+）：

```powershell
$million = 1_000_000
```

## $null

`$null` 不是空字串也不是 0，是「沒有值」：

```powershell
$x = $null
$x -eq $null    # True
$x -eq ""       # False
$x -eq 0        # False
```

檢查變數是否有值：

```powershell
if ($null -eq $x) {
    Write-Output "x 是 null"
}
```

**注意順序**：`$null -eq $x` 而不是 `$x -eq $null`。當 `$x` 是陣列時，後者的行為不同（它會逐元素比較）。

## 布林值

```powershell
$true   # 布林真
$false  # 布林假

# 以下值在條件判斷中被視為 false：
$null
0
""
@()  # 空陣列
```

```powershell
if ("") { "truthy" } else { "falsy" }   # 輸出: falsy
if (0)  { "truthy" } else { "falsy" }   # 輸出: falsy
if ("0"){ "truthy" } else { "falsy" }   # 輸出: truthy  ← 注意，"0" 是非空字串
```

## 多重賦值

```powershell
$a, $b, $c = 1, 2, 3
$a   # 1
$b   # 2
$c   # 3

# 交換
$a, $b = $b, $a
```

## 動手練習

```powershell
# 1. 把你的名字存成變數，用雙引號印出問候語
$yourName = "你的名字"
"你好，$yourName！今天是 $(Get-Date -Format 'yyyy-MM-dd')"

# 2. 測試型別標注
[int]$x = "42"    # 會成功嗎？
[int]$y = "hello" # 會成功嗎？

# 3. 測試 $null 行為
$z = $null
Write-Output ($null -eq $z)
Write-Output ($z -eq "")
```

## 自我檢核

- [ ] 理解單引號和雙引號的差異
- [ ] 知道如何用 `GetType()` 查型別
- [ ] 知道 `$null -eq $x` 要把 `$null` 放左邊
- [ ] 理解哪些值在條件判斷中是 falsy

→ [Ch 4 運算子全覽](./04-operators.md)
