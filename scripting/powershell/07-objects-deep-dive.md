# Ch 7 — 物件深入

> 目標：能用 `Get-Member` 探索任何物件的結構，自訂 `PSCustomObject`，理解 PowerShell 的物件模型。

## 一切都是 .NET 物件

PowerShell 建在 .NET 上，所有資料都是 .NET 物件：

```powershell
(Get-Process)[0].GetType().FullName
# System.Diagnostics.Process

"hello".GetType().FullName
# System.String

(Get-Date).GetType().FullName
# System.DateTime
```

這意味著你可以用 .NET 的方法：

```powershell
"hello world".ToUpper()           # HELLO WORLD
"hello world".Split(" ")          # @("hello", "world")
(Get-Date).AddDays(7)             # 7 天後的日期
[Math]::Sqrt(16)                  # 4  （靜態方法用 ::）
```

## Get-Member：探索物件

不確定物件有哪些屬性和方法？管道給 `Get-Member`：

```powershell
Get-Process | Get-Member

# 縮寫
Get-Process | gm
```

輸出分幾種 MemberType：

| MemberType | 說明 |
|-----------|------|
| `Property` | 真正的屬性 |
| `AliasProperty` | 屬性別名（如 `CPU = TotalProcessorTime`） |
| `NoteProperty` | 手動加上的屬性 |
| `Method` | 可呼叫的方法 |
| `ScriptProperty` | 用腳本計算的屬性 |

只看特定類型：

```powershell
Get-Process | Get-Member -MemberType Property
"hello" | Get-Member -MemberType Method
```

## PSCustomObject：自訂物件

最常用的方式是直接建立自訂物件：

```powershell
$server = [PSCustomObject]@{
    Name   = "web01"
    IP     = "192.168.1.10"
    Port   = 443
    IsUp   = $true
}

$server.Name   # web01
$server.IP     # 192.168.1.10
```

`PSCustomObject` 比 Hashtable 好用的地方：它保持屬性的**插入順序**，而且在 `Format-Table` 顯示時更漂亮。

建立物件陣列：

```powershell
$servers = @(
    [PSCustomObject]@{ Name = "web01"; IP = "192.168.1.10" }
    [PSCustomObject]@{ Name = "web02"; IP = "192.168.1.11" }
    [PSCustomObject]@{ Name = "db01";  IP = "192.168.1.20" }
)

$servers | Format-Table
$servers | Where-Object { $_.Name -like "web*" }
```

## 動態新增屬性

可以在物件建立後加屬性：

```powershell
$obj = [PSCustomObject]@{ Name = "test" }
$obj | Add-Member -MemberType NoteProperty -Name Status -Value "OK"
$obj.Status   # OK
```

也可以加方法：

```powershell
$obj | Add-Member -MemberType ScriptMethod -Name Greet -Value {
    "Hello, I am $($this.Name)"
}
$obj.Greet()   # Hello, I am test
```

`$this` 在 ScriptMethod 裡指向物件自身。

## Select-Object 建立物件

`Select-Object` 除了選欄位，加 `-Property` 可以計算新欄位：

```powershell
Get-Process | Select-Object Name, Id, @{
    Name = "MemMB"
    Expression = { [Math]::Round($_.WorkingSet64 / 1MB, 1) }
} | Sort-Object MemMB -Descending | Select-Object -First 5
```

`@{ Name = ...; Expression = { ... } }` 叫做**計算屬性**（Calculated Property），在 pipeline 裡建立新欄位的標準做法。

## 物件比較的注意事項

PowerShell 的 `-eq` 對物件比的是**參考**，不是內容：

```powershell
$a = [PSCustomObject]@{ X = 1 }
$b = [PSCustomObject]@{ X = 1 }

$a -eq $b   # False  （不同物件）
$a -eq $a   # True   （同一個物件）
```

比較值用 `.X -eq .X` 或用 `Compare-Object`：

```powershell
Compare-Object $a $b -Property X
# 沒有輸出 = 兩者相等
```

## 動手練習

```powershell
# 1. 探索 Get-Date 的方法
Get-Date | Get-Member -MemberType Method | Select-Object Name | Sort-Object Name

# 2. 建立一個伺服器清單，篩選出 Port > 80 的
$servers = @(
    [PSCustomObject]@{ Name = "web01"; Port = 443 }
    [PSCustomObject]@{ Name = "ftp01"; Port = 21  }
    [PSCustomObject]@{ Name = "smtp";  Port = 25  }
    [PSCustomObject]@{ Name = "https"; Port = 8443 }
)

$servers | Where-Object { $_.Port -gt 80 }

# 3. 用計算屬性加上一欄 "Protocol"
$servers | Select-Object Name, Port, @{
    Name = "Protocol"
    Expression = {
        if ($_.Port -eq 443 -or $_.Port -eq 8443) { "HTTPS" }
        elseif ($_.Port -eq 80) { "HTTP" }
        else { "Other" }
    }
}
```

## 自我檢核

- [ ] 能用 `Get-Member` 探索任何物件的屬性和方法
- [ ] 能建立 `PSCustomObject`
- [ ] 知道如何用計算屬性（`@{Name=...; Expression={...}}`）
- [ ] 理解 `-eq` 比的是物件參考，不是內容

→ [Ch 8 Pipeline 深入](./08-pipeline-deep-dive.md)
