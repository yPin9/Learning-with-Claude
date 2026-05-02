# Ch 2 — Pipeline 初探

> 目標：理解 PowerShell pipeline 傳的是物件而不是文字，這個差異讓它比 bash pipe 強大很多。

## Bash pipe vs PowerShell pipe

在 bash 裡，`|` 把左邊指令的 stdout（純文字）傳給右邊：

```bash
# bash：文字串，要靠 awk 解析欄位
ps aux | grep nginx | awk '{print $2}'
```

PowerShell 傳的是 **.NET 物件**：

```powershell
# PowerShell：傳的是 Process 物件，可以直接存取屬性
Get-Process | Where-Object { $_.Name -eq 'notepad' } | Stop-Process
```

不需要解析文字，`.Name`、`.Id`、`.CPU` 直接存取。這個差異是 PowerShell 的核心設計，值得多想一下。

## 物件有屬性和方法

當 `Get-Process` 輸出行程列表時，螢幕上看到的只是物件的「顯示格式」，不是物件本身。每個物件都有很多屬性：

```powershell
Get-Process | Get-Member
```

跑這個會看到 `Process` 物件的所有屬性（Property）和方法（Method）：

```
   TypeName: System.Diagnostics.Process

Name                       MemberType     Definition
----                       ----------     ----------
Handles                    Property       int Handles {get;}
Id                         Property       int Id {get;}
Name                       Property       string Name {get;}
CPU                        AliasProperty  CPU = TotalProcessorTime
WorkingSet                 AliasProperty  WorkingSet = WorkingSet64
...
```

用 `$_` 代表 pipeline 裡的當前物件，用 `.屬性名` 存取屬性：

```powershell
Get-Process | ForEach-Object { Write-Output $_.Name }
```

## 第一個 Pipeline

```powershell
# 取得所有行程，只看 CPU 超過 1 秒的，按 CPU 排序
Get-Process |
    Where-Object { $_.CPU -gt 1 } |
    Sort-Object CPU -Descending |
    Select-Object Name, Id, CPU
```

輸出：

```
Name       Id     CPU
----       --     ---
chrome   1234  45.234
...
```

這個 pipeline 做了四件事：
1. `Get-Process` 產生所有 Process 物件
2. `Where-Object` 過濾
3. `Sort-Object` 排序
4. `Select-Object` 只保留三個欄位

每個 cmdlet 只做一件事，組合起來能做複雜操作。

## PassThru 概念

有些 cmdlet 預設不輸出物件（只做動作），但加上 `-PassThru` 就會把物件傳到 pipeline：

```powershell
# 停止 notepad，沒有輸出
Stop-Process -Name notepad

# 停止 notepad，並把 Process 物件傳出去（可以繼續 pipeline）
Stop-Process -Name notepad -PassThru | Select-Object Name, ExitCode
```

## 把 Pipeline 折行

Pipeline 可以用換行讓它更易讀，但換行要放在 `|` 後面（或 `|` 前面加反引號）：

```powershell
# 正確：| 後面換行
Get-Process |
    Where-Object CPU -gt 1 |
    Sort-Object CPU -Descending

# 錯誤：| 前面換行會讓 PS 以為那行結束了
Get-Process
    | Where-Object CPU -gt 1   # 這行會報錯
```

## 動手練習

```powershell
# 1. 列出所有服務，只看正在執行的
Get-Service | Where-Object { $_.Status -eq 'Running' }

# 2. 列出前五個最耗記憶體的行程
Get-Process | Sort-Object WorkingSet -Descending | Select-Object -First 5 Name, WorkingSet

# 3. 把所有服務的名稱存成文字檔
Get-Service | Select-Object -ExpandProperty Name | Out-File C:\Temp\services.txt
```

試著在第 2 個指令後面加 `| Get-Member`，看看 `Select-Object` 輸出的物件長什麼樣。

## 自我檢核

- [ ] 理解 PS pipeline 傳物件不傳文字
- [ ] 能用 `Get-Member` 查物件有哪些屬性
- [ ] 能寫出基本的多步 pipeline（Get → Where → Sort → Select）
- [ ] 知道換行要放在 `|` 後面

→ [Ch 3 變數與資料型別](./03-variables-and-types.md)
