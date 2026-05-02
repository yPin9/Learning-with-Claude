# Ch 12 — 腳本參數化

> 目標：學會用 `[CmdletBinding()]` 和 `param()` 讓腳本接受命令列參數，加上型別限制、驗證、預設值，寫出和內建 cmdlet 一樣的使用體驗。

## 從腳本到 cmdlet 風格

沒有參數的腳本需要每次修改裡面的變數才能跑不同輸入，這很爛。用 `param()` 讓腳本接受命令列參數：

```powershell
# Get-DiskReport.ps1
param(
    [string]$ComputerName = "localhost",
    [string]$OutputPath   = "C:\Temp\report.csv"
)

Get-PSDrive -PSProvider FileSystem |
    Where-Object { $_.Used } |
    Select-Object Name, @{N='UsedGB'; E={ [Math]::Round($_.Used / 1GB, 1) }},
                        @{N='FreeGB'; E={ [Math]::Round($_.Free / 1GB, 1) }} |
    Export-Csv $OutputPath -NoTypeInformation

Write-Host "報告儲存到：$OutputPath"
```

執行：

```powershell
.\Get-DiskReport.ps1 -OutputPath C:\Reports\disk.csv
.\Get-DiskReport.ps1   # 用預設值
```

## [CmdletBinding()]

在 `param()` 前加 `[CmdletBinding()]`，腳本就變成「進階函式」，自動獲得：

- `-Verbose`（啟用 `Write-Verbose` 輸出）
- `-Debug`（啟用 `Write-Debug` 輸出）
- `-ErrorAction`、`-ErrorVariable`
- `-WhatIf`、`-Confirm`（需要自己加 `SupportsShouldProcess`）

```powershell
[CmdletBinding()]
param(
    [string]$Path
)

Write-Verbose "開始處理 $Path"
# ...
```

```powershell
.\script.ps1 -Path "C:\Temp" -Verbose   # 現在能看到 Verbose 訊息
```

## 型別限制和 Mandatory

```powershell
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ComputerName,

    [Parameter(Mandatory = $false)]
    [int]$Port = 443,

    [switch]$Force
)
```

`Mandatory = $true` 的參數如果沒有給，PS 會**互動式提示你輸入**，而不是直接報錯——適合互動式腳本，不適合自動化腳本（因為會卡住等輸入）。自動化腳本通常不用 `Mandatory`，改用 `ValidateNotNullOrEmpty`。

## 驗證屬性

```powershell
param(
    # 只接受列表內的值
    [ValidateSet("Development", "Staging", "Production")]
    [string]$Environment,

    # 數值範圍
    [ValidateRange(1, 65535)]
    [int]$Port,

    # 不能是空值
    [ValidateNotNullOrEmpty()]
    [string]$Name,

    # regex 驗證
    [ValidatePattern("^\d{4}-\d{2}-\d{2}$")]
    [string]$Date,

    # 自訂驗證邏輯
    [ValidateScript({
        if (Test-Path $_) { $true }
        else { throw "路徑不存在：$_" }
    })]
    [string]$LogPath
)
```

`ValidateSet` 還有一個好處：Tab 補全。使用者按 Tab 就能看到有哪些選項。

## 位置參數

```powershell
param(
    [Parameter(Position = 0)]
    [string]$Source,

    [Parameter(Position = 1)]
    [string]$Destination
)
```

加了 `Position`，呼叫時可以不寫參數名：

```powershell
.\Copy-Files.ps1 C:\Source C:\Dest   # 不用 -Source -Destination
```

## HelpMessage

```powershell
param(
    [Parameter(Mandatory = $true, HelpMessage = "輸入目標伺服器名稱或 IP")]
    [string]$ComputerName
)
```

當使用者沒給 `Mandatory` 的參數，PS 提示輸入時會順便顯示這段說明。

## 接受 Pipeline 輸入

```powershell
function Test-Server {
    [CmdletBinding()]
    param(
        [Parameter(ValueFromPipeline = $true,
                   ValueFromPipelineByPropertyName = $true)]
        [string]$ComputerName
    )

    process {
        Test-Connection -ComputerName $ComputerName -Count 1 -Quiet
    }
}

# 可以這樣用
"server01", "server02" | Test-Server

# ValueFromPipelineByPropertyName 讓物件的 ComputerName 屬性自動對應
Get-ADComputer -Filter * | Test-Server
```

## 完整範例

```powershell
# Invoke-SystemCheck.ps1
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory, ValueFromPipeline)]
    [ValidateNotNullOrEmpty()]
    [string]$ComputerName,

    [ValidateSet("Basic", "Full")]
    [string]$Mode = "Basic",

    [ValidateRange(1, 30)]
    [int]$TimeoutSeconds = 10,

    [switch]$ExportCsv,

    [ValidateScript({ Test-Path (Split-Path $_ -Parent) })]
    [string]$OutputPath = ".\report.csv"
)

process {
    if ($PSCmdlet.ShouldProcess($ComputerName, "執行系統檢查")) {
        Write-Verbose "正在檢查 $ComputerName（模式：$Mode）"
        # ... 執行邏輯
    }
}
```

## 動手練習

```powershell
# 建立 Get-FileStats.ps1，接受：
# -Path：要掃描的目錄（Mandatory，驗證路徑存在）
# -Extension：副檔名篩選（預設 "*"）
# -TopN：顯示前幾名（範圍 1-100，預設 10）
# 輸出：最大的 TopN 個檔案（名稱、大小 MB、最後修改時間）

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path $_ -PathType Container })]
    [string]$Path,

    [string]$Extension = "*",

    [ValidateRange(1, 100)]
    [int]$TopN = 10
)

$filter = if ($Extension -eq "*") { "*" } else { "*.$Extension" }
Get-ChildItem -Path $Path -Filter $filter -Recurse -File -ErrorAction SilentlyContinue |
    Sort-Object Length -Descending |
    Select-Object -First $TopN Name,
        @{N='SizeMB'; E={ [Math]::Round($_.Length / 1MB, 2) }},
        LastWriteTime |
    Format-Table -AutoSize
```

## 自我檢核

- [ ] 知道 `[CmdletBinding()]` 自動加上 `-Verbose`、`-Debug`、`-ErrorAction`
- [ ] 能用 `ValidateSet`、`ValidateRange`、`ValidateScript` 做輸入驗證
- [ ] 理解 `Mandatory` 的互動提示行為，知道在自動化腳本裡要謹慎使用
- [ ] 能讓函式接受 pipeline 輸入（`ValueFromPipeline`）

→ [Ch 13 錯誤處理](./13-error-handling.md)
