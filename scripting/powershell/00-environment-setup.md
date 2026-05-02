# Ch 0 — 環境建置

> 目標：在 Windows 11 上裝好 PowerShell 7、VSCode，設定 Profile，讓後面每一章的範例都能直接跑。

## PowerShell 7 vs Windows PowerShell 5.1

Windows 內建的是 **Windows PowerShell 5.1**（`powershell.exe`），這是舊版、只跑在 .NET Framework 上、微軟已停止新功能開發。我們學的是 **PowerShell 7**（`pwsh.exe`），跑在 .NET 8 上，跨平台、持續更新。

```
powershell.exe   →  Windows PowerShell 5.1（別用這個學習）
pwsh.exe         →  PowerShell 7（我們用這個）
```

兩者大部分語法相同，但 PS 7 有很多改進：平行 foreach、三元運算子、null coalescing、更好的錯誤訊息。如果公司環境只有 5.1，差異我們到時候會特別標注。

## 安裝 PowerShell 7

最快的方式用 winget：

```powershell
winget install Microsoft.PowerShell
```

裝完後重開終端，輸入 `pwsh` 就能進 PowerShell 7。確認版本：

```powershell
$PSVersionTable
```

輸出應該看到 `PSVersion` 是 `7.x.x`。

## 安裝 VSCode 與擴充套件

```powershell
winget install Microsoft.VisualStudioCode
```

裝完後在 VSCode 裡裝兩個擴充套件（`Ctrl+Shift+X` 搜尋）：

- **PowerShell**（by Microsoft）— 語法高亮、IntelliSense、整合終端
- **GitLens**（可選）— 如果你用 Git 管腳本

裝好 PowerShell 擴充後，打開任何 `.ps1` 檔，右下角會顯示目前使用的 PS 版本。確認它選的是 PowerShell 7 而不是 5.1。

## 執行政策

第一次跑 `.ps1` 腳本多半會被擋，因為預設執行政策是 `Restricted`：

```powershell
Get-ExecutionPolicy
# 輸出: Restricted
```

開發環境改成 `RemoteSigned`（本機腳本不需簽署，從網路下載的才需要）：

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

`-Scope CurrentUser` 只改你的帳號，不影響其他使用者。

## 更新說明文件

第一次用 `Get-Help` 前，先把離線文件拉下來：

```powershell
Update-Help -Force -UICulture en-US
```

這個指令需要網路，跑一次就夠。之後每隔幾個月可以再跑一次更新。

## Profile：你的 PS 個人設定

Profile 是每次啟動 PS 時自動執行的腳本，用來設定別名、自訂 Prompt、載入常用模組。

查看 Profile 路徑：

```powershell
$PROFILE
# 例：C:\Users\yourname\Documents\PowerShell\Microsoft.PowerShell_profile.ps1
```

建立並編輯 Profile：

```powershell
New-Item -Path $PROFILE -ItemType File -Force
code $PROFILE   # 用 VSCode 開啟
```

加入一個簡單的測試設定：

```powershell
# 在 profile.ps1 裡加入這行
Write-Host "PS $($PSVersionTable.PSVersion) ready" -ForegroundColor Green
```

存檔後重開 PS，應該會看到那行綠色文字。

## 動手練習

1. 跑 `$PSVersionTable`，確認版本是 7.x
2. 跑 `Get-ExecutionPolicy`，確認是 `RemoteSigned`
3. 打開 Profile，加入自訂的 Prompt 函式：

```powershell
function prompt {
    $path = (Get-Location).Path
    "PS [$path]> "
}
```

存檔重開，你的 Prompt 會變成 `PS [C:\Users\you]>` 的格式。

## 自我檢核

- [ ] `pwsh` 能啟動 PowerShell 7
- [ ] VSCode 的 PowerShell 擴充指向 PS 7
- [ ] `Get-ExecutionPolicy` 回傳 `RemoteSigned`
- [ ] Profile 存在且能被載入

Shell 準備好了，下一章開始學怎麼和它溝通。

→ [Ch 1 Shell 的思維方式](./01-shell-mindset.md)
