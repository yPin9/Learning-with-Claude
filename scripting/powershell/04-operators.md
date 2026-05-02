# Ch 4 — 運算子全覽

> 目標：掌握 PowerShell 的比較、邏輯、字串、型別等運算子，特別是 `-eq` 這類字母運算子與 bash/Python 的差異。

## 算術運算子

```powershell
5 + 3    # 8
5 - 3    # 2
5 * 3    # 15
5 / 3    # 1.6666...
5 % 3    # 2  （取餘數）
5 -shl 2 # 20 （左移 2 位，= 5 * 4）
5 -shr 1 # 2  （右移 1 位，= 5 / 2）

# 字串也可以 + 和 *
"ab" + "cd"   # abcd
"ha" * 3      # hahaha
```

## 比較運算子

PowerShell **不用** `==`、`!=`、`<`、`>`，改用字母形式（避免跟重導向符號衝突）：

| 運算子 | 意義 | 範例 |
|--------|------|------|
| `-eq`  | 等於 | `5 -eq 5` → `True` |
| `-ne`  | 不等於 | `5 -ne 3` → `True` |
| `-lt`  | 小於 | `3 -lt 5` → `True` |
| `-le`  | 小於等於 | `5 -le 5` → `True` |
| `-gt`  | 大於 | `5 -gt 3` → `True` |
| `-ge`  | 大於等於 | `5 -ge 5` → `True` |

字串比較**不分大小寫**（預設）：

```powershell
"hello" -eq "HELLO"   # True
"hello" -ne "world"   # True
```

要區分大小寫，加 `c` 前綴：

```powershell
"hello" -ceq "HELLO"  # False
"hello" -ceq "hello"  # True
```

## 邏輯運算子

```powershell
$true  -and $false   # False
$true  -or  $false   # True
-not $true            # False
$true  -xor $true    # False  （互斥或）
```

也可以用 `!` 代替 `-not`：

```powershell
!$true   # False
```

## 字串比較運算子

| 運算子 | 意義 | 範例 |
|--------|------|------|
| `-like` | 萬用字元比對（`*` 和 `?`） | `"hello" -like "h*"` → `True` |
| `-notlike` | 不符合萬用字元 | |
| `-match` | 正規表示式比對 | `"hello" -match "^h"` → `True` |
| `-notmatch` | 不符合 regex | |
| `-contains` | 集合包含某值 | `@(1,2,3) -contains 2` → `True` |
| `-notcontains` | 集合不包含 | |
| `-in` | 值在集合中 | `2 -in @(1,2,3)` → `True` |
| `-notin` | 值不在集合中 | |

`-like` 用簡單的萬用字元，`-match` 用完整 regex。選哪個看需求。

```powershell
# -like 範例
"PowerShell" -like "Power*"    # True
"PowerShell" -like "?ower*"    # True  (? 匹配單一字元)

# -contains 和 -in 注意方向
@("apple","banana") -contains "apple"   # True  (集合 -contains 值)
"apple" -in @("apple","banana")         # True  (值 -in 集合)
```

## 型別運算子

```powershell
42 -is [int]         # True
42 -is [string]      # False
"hi" -is [string]    # True
42 -isnot [string]   # True

# -as：強制轉型，失敗時回傳 $null 而不是報錯
"42" -as [int]       # 42
"hi" -as [int]       # $null（轉不了）
```

`-as` 比直接寫 `[int]"hi"` 安全，因為後者在轉型失敗時會拋出例外。

## 範圍運算子

```powershell
1..10         # 陣列 [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
10..1         # 倒序陣列
'a'..'e'      # 字元陣列 [a, b, c, d, e]

# 常用在 for 迴圈
foreach ($i in 1..5) { Write-Output $i }
```

## 三元運算子（PS 7+）

```powershell
$score = 85
$result = $score -ge 60 ? "Pass" : "Fail"
Write-Output $result   # Pass
```

這是 PS 7 加入的，5.1 沒有，腳本若要跨版本就別用。

## Null 合併運算子（PS 7+）

```powershell
$name = $null
$displayName = $name ?? "Guest"   # 如果 $name 是 null，用 "Guest"
Write-Output $displayName   # Guest

# Null 條件賦值
$x = $null
$x ??= "default"   # 如果 $x 是 null，賦值 "default"
```

## 動手練習

```powershell
# 1. 測試比較（猜猜看輸出什麼）
"PowerShell" -like "power*"    # 預測？
"PowerShell" -clike "power*"   # 預測？

# 2. 測試 -as 的安全轉型
$a = "123" -as [int]
$b = "abc" -as [int]
Write-Output "a=$a, b=$($b -eq $null)"

# 3. 測試範圍運算子
$nums = 1..10
$nums | Measure-Object -Sum   # 加總是多少？
```

## 自我檢核

- [ ] 知道 `-eq` 而不是 `==`
- [ ] 理解 `-like` 和 `-match` 的差異（萬用字元 vs regex）
- [ ] 知道 `-contains` 和 `-in` 的主詞方向相反
- [ ] 知道 `-as` 轉型失敗回傳 `$null` 而不是報錯

→ [Ch 5 流程控制](./05-control-flow.md)
