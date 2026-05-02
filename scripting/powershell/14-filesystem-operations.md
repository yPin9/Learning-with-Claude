# Ch 14 — 檔案系統操作

> 目標：熟練使用 `Get-ChildItem`、`Copy/Move/Remove-Item`、`New-Item`、`Test-Path`，能寫出健壯的檔案管理腳本。

## Provider 概念

PowerShell 把很多「存放東西的地方」都抽象成 **Provider**，用統一的 `Item` 系列 cmdlet 操作：

```
FileSystem  →  檔案和目錄
Registry    →  登錄檔 (HKLM:, HKCU:)
Environment →  環境變數 (Env:)
Certificate →  憑證 (Cert:)
```

```powershell
Get-PSDrive              # 列出所有 PSDrive
Get-PSProvider           # 列出所有 Provider
```

所以 `Get-Item`、`Get-ChildItem`、`New-Item` 在磁碟上和在登錄檔裡的用法幾乎一樣。

## Get-ChildItem：列目錄

```powershell
# 基本列出
Get-ChildItem C:\Temp           # 或 ls, dir
Get-ChildItem .                 # 當前目錄

# 遞迴
Get-ChildItem C:\Logs -Recurse

# 過濾
Get-ChildItem C:\Logs -Filter "*.log"       # 用 OS 篩選（快）
Get-ChildItem C:\Logs -Include "*.log","*.txt" -Recurse  # 多種副檔名
Get-ChildItem C:\Logs -Exclude "archive*"   # 排除

# 只要檔案（不要目錄）
Get-ChildItem C:\Logs -File

# 只要目錄
Get-ChildItem C:\Logs -Directory

# 包含隱藏和系統檔
Get-ChildItem C:\Windows -Force

# 深度限制（PS 5+）
Get-ChildItem C:\Logs -Recurse -Depth 2
```

`-Filter` 比 `-Include` 快（直接交給 OS 篩選），但只能給一個 pattern 且不支援複雜 regex。

## Get-Item 和 Get-ItemProperty

```powershell
# 取得單一項目的物件
$f = Get-Item C:\Windows\System32\notepad.exe
$f.Length            # 檔案大小（bytes）
$f.LastWriteTime     # 最後修改時間
$f.Extension         # .exe

# 查詢多個路徑
Get-Item C:\Windows, C:\Users
```

## Test-Path：檢查存在

```powershell
Test-Path C:\Temp              # True/False
Test-Path C:\Temp -PathType Container   # 只檢查是否為目錄
Test-Path C:\Temp\file.txt -PathType Leaf       # 只檢查是否為檔案

# 腳本裡的標準用法
if (-not (Test-Path C:\Temp)) {
    New-Item C:\Temp -ItemType Directory
}
```

## New-Item：建立檔案和目錄

```powershell
# 建立目錄
New-Item C:\Temp\Logs -ItemType Directory

# 建立檔案
New-Item C:\Temp\test.txt -ItemType File

# 建立並寫入內容
New-Item C:\Temp\test.txt -ItemType File -Value "Hello, World!"

# 建立整個路徑（-Force 連父目錄一起建）
New-Item C:\Temp\a\b\c -ItemType Directory -Force
```

## Copy-Item 和 Move-Item

```powershell
# 複製檔案
Copy-Item C:\Source\file.txt C:\Dest\

# 複製目錄（要加 -Recurse）
Copy-Item C:\Source C:\Dest -Recurse

# 複製後強制覆蓋
Copy-Item C:\Source\file.txt C:\Dest\file.txt -Force

# 移動
Move-Item C:\Temp\old.txt C:\Archive\

# 重命名（Move-Item 也可以做）
Rename-Item C:\Temp\old.txt new.txt
Move-Item C:\Temp\old.txt C:\Temp\new.txt   # 等同
```

## Remove-Item：刪除

```powershell
# 刪除檔案
Remove-Item C:\Temp\file.txt

# 刪除目錄（要加 -Recurse）
Remove-Item C:\Temp\OldLogs -Recurse

# 強制刪除（包括唯讀）
Remove-Item C:\Temp\locked.txt -Force

# 用 -WhatIf 先預覽會刪什麼
Remove-Item C:\Temp\* -WhatIf

# 刪除前不確認（自動化腳本裡用）
Remove-Item C:\Temp\* -Confirm:$false
```

**刪除前務必用 `-WhatIf` 確認，沒有回收桶。**

## 讀寫檔案內容

```powershell
# 讀全部（傳回字串陣列，每行一個）
$lines = Get-Content C:\Logs\app.log

# 讀成一個大字串
$text = Get-Content C:\file.txt -Raw

# 讀最後 N 行（大檔案效率好）
Get-Content C:\Logs\big.log -Tail 50

# 寫檔案（覆蓋）
Set-Content C:\Temp\out.txt -Value "Hello"
"Hello" | Set-Content C:\Temp\out.txt   # 等同

# 附加
Add-Content C:\Temp\log.txt -Value "$(Get-Date): event"
"新行" | Add-Content C:\Temp\log.txt
```

## 實用場景：清理舊檔案

```powershell
# 刪除 30 天前的 .log 檔
$cutoff = (Get-Date).AddDays(-30)
Get-ChildItem C:\Logs -Filter "*.log" -Recurse |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    Remove-Item -WhatIf   # 先 WhatIf 確認，確認後拿掉
```

## 動手練習

```powershell
# 1. 找出 C:\Windows\System32 裡最大的 10 個檔案
Get-ChildItem C:\Windows\System32 -File |
    Sort-Object Length -Descending |
    Select-Object -First 10 Name,
        @{N='SizeMB'; E={ [Math]::Round($_.Length / 1MB, 2) }}

# 2. 建立一個備份腳本：把 C:\Temp\*.txt 複製到 C:\Backup\<日期> 目錄
$date = Get-Date -Format "yyyy-MM-dd"
$dest = "C:\Backup\$date"
New-Item $dest -ItemType Directory -Force
Get-ChildItem C:\Temp -Filter "*.txt" |
    Copy-Item -Destination $dest
Write-Host "備份到 $dest 完成"
```

## 自我檢核

- [ ] 知道 `-Filter` 比 `-Include` 快，但 `-Include` 支援多個 pattern
- [ ] 習慣在 `Remove-Item` 前先用 `-WhatIf` 確認
- [ ] 能用 `Get-Content -Tail` 讀大檔案的最後幾行
- [ ] 知道 `Copy-Item` 複製目錄要加 `-Recurse`

→ [Ch 15 文字與結構化資料](./15-structured-data.md)
