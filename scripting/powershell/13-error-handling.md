# Ch 13 — 錯誤處理

> 目標：理解 terminating vs non-terminating 錯誤的差異，掌握 `try/catch/finally`、`-ErrorAction`、`$Error` 的正確使用方式。

## 兩種錯誤

PowerShell 有兩種錯誤，行為完全不同：

**Non-terminating（不終止錯誤）**：印出錯誤訊息後繼續執行，這是多數 cmdlet 的預設行為：

```powershell
Get-Item "不存在的路徑", "C:\Windows"
# 錯誤：找不到 "不存在的路徑"
# 然後繼續：顯示 C:\Windows 的資訊
```

**Terminating（終止錯誤）**：立刻停止當前作用域，像其他語言的 exception：

```powershell
# 除以零是 terminating
1 / 0   # 直接跳到 catch
```

這個設計的問題：很多你以為會「報錯停止」的 cmdlet 其實是 non-terminating，腳本會繼續跑，可能造成無聲的錯誤。

## -ErrorAction：控制錯誤行為

每個 cmdlet 都接受 `-ErrorAction` 參數：

| 值 | 行為 |
|----|------|
| `Continue`（預設） | 印出錯誤，繼續執行 |
| `Stop` | 把 non-terminating 轉成 terminating，讓 try/catch 能捕捉 |
| `SilentlyContinue` | 靜默忽略錯誤，繼續執行 |
| `Ignore` | 完全忽略（不進 `$Error`） |
| `Inquire` | 互動式詢問要不要繼續 |

**最重要的用法**：在 `try/catch` 裡要捕捉 cmdlet 錯誤，必須加 `-ErrorAction Stop`：

```powershell
try {
    Get-Item "不存在" -ErrorAction Stop   # ← 沒有這個，catch 不會被觸發
} catch {
    "捕捉到錯誤：$($_.Exception.Message)"
}
```

## try / catch / finally

```powershell
try {
    # 可能出錯的程式碼
    $content = Get-Content "不存在.txt" -ErrorAction Stop
    "內容：$content"
} catch [System.IO.FileNotFoundException] {
    "檔案找不到"
} catch [System.UnauthorizedAccessException] {
    "沒有存取權限"
} catch {
    # 捕捉所有其他錯誤
    "未預期錯誤：$($_.Exception.Message)"
    Write-Error $_ -ErrorAction Continue   # 重新拋出（記錄）
} finally {
    # 不管有沒有錯，都會執行（通常用來清理資源）
    "清理完成"
}
```

`$_` 在 catch 區塊裡是 `ErrorRecord` 物件，有以下常用屬性：

```powershell
$_.Exception.Message   # 錯誤訊息
$_.Exception.GetType().FullName   # 例外型別名
$_.InvocationInfo.ScriptLineNumber  # 哪一行出錯
$_.CategoryInfo.Category           # 錯誤類別
```

## 查例外型別

不知道要 catch 什麼型別？先讓它報錯，然後查：

```powershell
try {
    Get-Item "不存在" -ErrorAction Stop
} catch {
    $_.Exception.GetType().FullName
    # System.Management.Automation.ItemNotFoundException
}
```

## $Error：錯誤歷史

`$Error` 是自動變數，存著這個 session 裡最近的錯誤（預設存 256 筆）：

```powershell
# 最近一個錯誤
$Error[0]

# 最近 5 個
$Error[0..4]

# 清空
$Error.Clear()

# 設定保留數量（profile 裡設定）
$MaximumErrorCount = 512
```

## $ErrorActionPreference

改全局預設行為（通常只在腳本頂端設）：

```powershell
$ErrorActionPreference = "Stop"   # 讓所有 cmdlet 預設 Stop
```

生產腳本通常這樣設，確保任何錯誤都會被捕捉，不會無聲失敗。

## throw：手動拋出錯誤

```powershell
function Get-PositiveNumber {
    param([int]$n)
    if ($n -le 0) {
        throw "數字必須是正整數，輸入了：$n"
    }
    $n
}

try {
    Get-PositiveNumber -n -5
} catch {
    "錯誤：$($_.Exception.Message)"
}
```

## -ErrorVariable：捕捉到變數

```powershell
Get-Item "不存在", "C:\Windows" -ErrorAction SilentlyContinue -ErrorVariable errs

if ($errs) {
    "有 $($errs.Count) 個錯誤"
    $errs[0].Exception.Message
}
```

`-ErrorVariable` 把錯誤存進變數，同時可以繼續執行。

## 完整錯誤處理模板

生產腳本的標準寫法：

```powershell
[CmdletBinding()]
param([string]$Path)

$ErrorActionPreference = "Stop"

try {
    Write-Verbose "開始處理 $Path"

    $files = Get-ChildItem $Path -File
    foreach ($f in $files) {
        Write-Verbose "處理：$($f.Name)"
        # 處理邏輯...
    }

    Write-Host "完成，共處理 $($files.Count) 個檔案" -ForegroundColor Green

} catch [System.IO.DirectoryNotFoundException] {
    Write-Error "目錄不存在：$Path"
    exit 1
} catch {
    Write-Error "未預期錯誤：$($_.Exception.Message)"
    Write-Error "位置：$($_.InvocationInfo.ScriptLineNumber) 行"
    exit 1
} finally {
    Write-Verbose "腳本結束"
}
```

## 動手練習

```powershell
# 1. 測試 non-terminating vs terminating 差異
# 先跑這個（不加 -ErrorAction Stop）
Get-Item "不存在" 
Write-Host "這行會執行嗎？"

# 再加上 try/catch 但不加 -ErrorAction Stop
try {
    Get-Item "不存在"
    Write-Host "try 裡這行會執行嗎？"
} catch {
    "進入 catch 了嗎？"
}

# 最後加上 -ErrorAction Stop
try {
    Get-Item "不存在" -ErrorAction Stop
} catch {
    "進入 catch：$($_.Exception.Message)"
}
```

## 自我檢核

- [ ] 理解 non-terminating 和 terminating 的差異
- [ ] 知道要讓 `try/catch` 捕捉 cmdlet 錯誤，必須加 `-ErrorAction Stop`
- [ ] 能在 `catch` 裡用 `$_.Exception.GetType().FullName` 查例外型別
- [ ] 知道 `$ErrorActionPreference = "Stop"` 的作用

→ [Ch 14 檔案系統操作](./14-filesystem-operations.md)
