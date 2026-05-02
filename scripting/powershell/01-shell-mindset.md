# Ch 1 — Shell 的思維方式

> 目標：學會用 `Get-Help` 和 `Get-Command` 自我導航，理解 Verb-Noun 命名慣例，不靠 Google 就能找到需要的 cmdlet。

## Verb-Noun：PowerShell 的命名法

PowerShell 所有內建指令都叫 **cmdlet**（發音：command-let），格式固定是 `動詞-名詞`：

```
Get-Process      # 取得行程列表
Stop-Process     # 停止行程
Start-Service    # 啟動服務
Get-ChildItem    # 列出目錄內容（等同 ls / dir）
```

動詞是有限集合，微軟定義了約 100 個標準動詞：

```powershell
Get-Verb
# 列出所有認可的動詞，如 Get, Set, New, Remove, Start, Stop, Test, Invoke...
```

記住幾個常用動詞對，基本上就能猜出大部分 cmdlet 的名字：

| 動詞 | 反義 |
|------|------|
| Get  | Set  |
| New  | Remove |
| Start | Stop |
| Enable | Disable |
| Import | Export |
| Add | Remove |

實際上你不需要全部記住，因為有 `Get-Command`。

## Get-Command：找 cmdlet

不知道 cmdlet 叫什麼名字？用 `Get-Command` 搜：

```powershell
# 找所有和 "service" 有關的 cmdlet
Get-Command -Noun Service

# 找所有用 Get 動詞的 cmdlet
Get-Command -Verb Get

# 模糊搜尋
Get-Command *network*

# 看某個模組提供什麼 cmdlet
Get-Command -Module NetTCPIP
```

## Get-Help：查文件

找到 cmdlet 名字後，查用法：

```powershell
Get-Help Get-Process

# 看詳細說明
Get-Help Get-Process -Detailed

# 看範例（最實用）
Get-Help Get-Process -Examples

# 看完整文件（含參數說明）
Get-Help Get-Process -Full

# 在瀏覽器開網頁版
Get-Help Get-Process -Online
```

`-Examples` 是最快上手的方式，微軟的範例通常都很實用。

一個常見誤解：Help 文件不會自己更新，要手動跑 `Update-Help`。如果你看到「This cmdlet does not have help content」，就是還沒更新。

## Tab 補全

PowerShell 7 的 Tab 補全很強：

```powershell
Get-Ch<Tab>        # 補全 cmdlet 名稱
Get-ChildItem -<Tab>  # 列出所有參數
[System.<Tab>      # 補全 .NET 型別
```

按 `Ctrl+Space` 可以強制觸發補全清單（在 VSCode 終端裡）。

## 別名（Alias）

很多常用 cmdlet 有短別名，來自 Unix 或 DOS 傳統：

```powershell
ls        # = Get-ChildItem
cd        # = Set-Location
pwd       # = Get-Location
cat       # = Get-Content
echo      # = Write-Output
ps        # = Get-Process
kill      # = Stop-Process
```

查某個別名指向什麼：

```powershell
Get-Alias ls
# Alias  ls -> Get-ChildItem
```

列出所有別名：

```powershell
Get-Alias
```

**別在腳本裡用別名**。別名是為了互動式使用方便，腳本裡要用完整 cmdlet 名稱，不然換個人的電腦可能別名不存在就壞了。

## 歷史記錄

```powershell
Get-History          # 列出本次 session 的指令歷史
Invoke-History 42    # 重跑第 42 條歷史指令
# 或用 r 42 (Invoke-History 的別名)

# 搜尋歷史（上下鍵 + Ctrl+R 逆向搜尋）
```

PS 7 預設用 PSReadLine 模組，提供更好的歷史搜尋體驗。

## 常用基本操作

```powershell
# 清螢幕
Clear-Host   # 或 cls

# 列出目前位置
Get-Location  # 或 pwd

# 切換目錄
Set-Location C:\Users    # 或 cd C:\Users
Set-Location ..          # 回上一層
Set-Location ~           # 回家目錄

# 列出檔案
Get-ChildItem            # 或 ls / dir
Get-ChildItem -Force     # 包含隱藏檔
```

## 動手練習

不要跳過這步，實際跑以下指令感受一下：

```powershell
# 找所有和 "User" 有關的 cmdlet
Get-Command -Noun *User*

# 查 Get-ChildItem 的範例
Get-Help Get-ChildItem -Examples

# 確認 ls 是什麼的別名
Get-Alias ls

# 看目前有哪些 PSDrive（磁碟）
Get-PSDrive
```

最後一個指令的輸出會讓你意外——除了 C:、D: 這些磁碟，還有 HKLM:（登錄檔）、Env:（環境變數）等。這就是 PowerShell Provider 的概念，我們後面會詳細講。

## 自我檢核

- [ ] 知道用 `Get-Command -Noun X` 找 cmdlet
- [ ] 知道用 `Get-Help X -Examples` 查範例
- [ ] 理解 Verb-Noun 命名慣例
- [ ] 知道別名存在但腳本裡不該用

→ [Ch 2 Pipeline 初探](./02-pipeline-intro.md)
