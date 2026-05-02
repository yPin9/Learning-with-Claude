# Ch 6 — 函式與作用域

> 目標：學會定義帶參數的函式，理解 local/script/global 作用域，避免腳本互相污染變數。

## 基本函式

```powershell
function Get-Greeting {
    param($Name)
    return "Hello, $Name!"
}

Get-Greeting -Name "Alice"    # Hello, Alice!
Get-Greeting "Alice"          # 位置參數，同上
```

函式命名慣例和 cmdlet 一樣用 Verb-Noun（`Get-`、`Set-`、`Invoke-` 等）。

`return` 可以省略——PowerShell 函式會把**所有沒被捕捉的輸出**當回傳值：

```powershell
function Get-Double {
    param([int]$n)
    $n * 2   # 這行的結果自動成為回傳值
}

$result = Get-Double 5
$result   # 10
```

這個行為讓人意外：如果函式裡有任何「漏出去」的輸出，它都會被加進回傳值。

```powershell
function Bad-Example {
    Write-Output "這行會跑進回傳值！"
    42
}

$x = Bad-Example
$x   # 輸出: @("這行會跑進回傳值！", 42) ← 陣列！
```

**要印狀態訊息用 `Write-Host` 或 `Write-Verbose`，不要用 `Write-Output`**，否則會污染回傳值。

## param 區塊

完整的參數宣告放在函式最前面的 `param()` 區塊：

```powershell
function Invoke-Report {
    param(
        [string]$Server,
        [int]$Port = 443,     # 預設值
        [switch]$Verbose      # switch 類型：不需要給值，有就是 $true
    )

    "連線到 ${Server}:${Port}"
    if ($Verbose) {
        "詳細模式開啟"
    }
}

Invoke-Report -Server "web01" -Port 8080
Invoke-Report -Server "web01" -Verbose  # Port 預設 443
```

`[switch]` 參數就像旗標，命令列上加了就是 `$true`，不加就是 `$false`。

## 作用域

PowerShell 有三個主要作用域：

```
Global  ← 整個 PS session
  └── Script  ← 單一 .ps1 檔
        └── Local  ← 函式內
```

```powershell
$x = "global"

function Test-Scope {
    $x = "local"       # 建立新的 local 變數，不影響 global
    Write-Output $x    # local
}

Test-Scope
Write-Output $x        # global（沒被改動）
```

想在函式裡修改外部的變數，要明確指定作用域：

```powershell
$counter = 0

function Increment {
    $script:counter++   # 修改 script 作用域的 $counter
}

Increment
Increment
$counter   # 2
```

| 前綴 | 範圍 |
|------|------|
| `$local:var` | 目前函式（預設） |
| `$script:var` | 目前 .ps1 檔案 |
| `$global:var` | 整個 PS session |

**一般不要用 `$global:`**，它會在 session 裡留下污染。`$script:` 在同一個腳本裡共享狀態是 OK 的。

## 管線輸入

函式也可以接受 pipeline 輸入：

```powershell
function Get-UpperName {
    process {
        $_.Name.ToUpper()   # $_ 是 pipeline 當前物件
    }
}

Get-Process | Get-UpperName | Select-Object -First 5
```

`process` 區塊對 pipeline 裡的每個物件執行一次。完整的函式可以有 `begin`（執行一次初始化）、`process`（每個物件）、`end`（收尾）三個區塊。

## $using: 在遠端和 Job 裡

當你用 `Invoke-Command` 跑遠端腳本或 `Start-Job` 跑背景工作時，本機變數不會自動帶過去——要用 `$using:`：

```powershell
$threshold = 100MB

Invoke-Command -ComputerName Server01 {
    Get-ChildItem C:\Logs |
        Where-Object { $_.Length -gt $using:threshold }
}
```

## 動手練習

```powershell
# 1. 寫一個函式：輸入路徑，回傳目錄下所有 .log 檔的總大小（bytes）
function Get-LogSize {
    param([string]$Path = "C:\Windows\Logs")
    $files = Get-ChildItem -Path $Path -Filter "*.log" -Recurse -ErrorAction SilentlyContinue
    $files | Measure-Object -Property Length -Sum | Select-Object -ExpandProperty Sum
}

# 2. 測試作用域隔離
$msg = "outside"
function Test {
    $msg = "inside"
    Write-Host "函式內：$msg"
}
Test
Write-Host "函式外：$msg"   # 應該還是 "outside"
```

## 自我檢核

- [ ] 理解函式的「所有輸出都是回傳值」——`Write-Host` 不進回傳值，`Write-Output` 會
- [ ] 會用 `[switch]` 參數
- [ ] 理解 `$local:` / `$script:` / `$global:` 作用域前綴
- [ ] 知道 `$using:` 用在遠端和 Job 裡

→ [Ch 7 物件深入](./07-objects-deep-dive.md)
