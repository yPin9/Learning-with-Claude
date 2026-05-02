# Ch 29 — REST API 整合與安全實踐

> 目標：用 `Invoke-RestMethod` 呼叫 REST API，掌握 `SecureString` 保護憑證，理解 ExecutionPolicy 和腳本簽署。

## Invoke-RestMethod vs Invoke-WebRequest

兩個 cmdlet 都能發 HTTP 請求，差異在回傳值：

| | `Invoke-RestMethod` | `Invoke-WebRequest` |
|--|-------------------|-------------------|
| 回傳 | 自動解析 JSON/XML → 物件 | `WebResponseObject`（含 Headers、Content 等）|
| 適合 | REST API（JSON 回應） | 需要 Response Header、爬蟲 |

```powershell
# Invoke-RestMethod 自動解析 JSON
$data = Invoke-RestMethod -Uri "https://api.github.com/users/microsoft"
$data.login        # microsoft
$data.public_repos # 數字

# Invoke-WebRequest 回傳完整 Response
$resp = Invoke-WebRequest -Uri "https://api.github.com/users/microsoft"
$resp.StatusCode       # 200
$resp.Headers          # Response Headers
$resp.Content          # 原始 JSON 字串
$resp.Content | ConvertFrom-Json   # 手動解析
```

## 基本 REST API 呼叫

```powershell
# GET
$result = Invoke-RestMethod -Uri "https://jsonplaceholder.typicode.com/posts/1" -Method Get

# POST（傳送 JSON body）
$body = @{
    title  = "Test Post"
    body   = "Content here"
    userId = 1
} | ConvertTo-Json

$response = Invoke-RestMethod `
    -Uri "https://jsonplaceholder.typicode.com/posts" `
    -Method Post `
    -Body $body `
    -ContentType "application/json"

# PUT / PATCH / DELETE
Invoke-RestMethod -Uri "https://api.example.com/items/1" -Method Delete
```

## 認證：Bearer Token

```powershell
$token = "your-api-token-here"

$headers = @{
    Authorization = "Bearer $token"
    Accept        = "application/json"
}

Invoke-RestMethod `
    -Uri "https://api.example.com/data" `
    -Headers $headers `
    -Method Get
```

## 認證：Basic Auth

```powershell
# 方法 1：-Credential
$cred = Get-Credential
Invoke-RestMethod -Uri "https://api.example.com" -Credential $cred

# 方法 2：手動建構 Base64 Header
$username = "user"
$password = "pass"
$pair = "${username}:${password}"
$encoded = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($pair))

Invoke-RestMethod `
    -Uri "https://api.example.com" `
    -Headers @{ Authorization = "Basic $encoded" }
```

## 錯誤處理

```powershell
try {
    $result = Invoke-RestMethod `
        -Uri "https://api.example.com/resource" `
        -ErrorAction Stop
} catch [System.Net.WebException] {
    $statusCode = $_.Exception.Response.StatusCode.value__
    Write-Error "HTTP 錯誤 $statusCode：$($_.Exception.Message)"
} catch {
    Write-Error "請求失敗：$($_.Exception.Message)"
}
```

PS 7 對 HTTP 錯誤的處理更好，可以用 `-StatusCodeVariable` 取得狀態碼而不拋出例外：

```powershell
$result = Invoke-RestMethod -Uri "..." -StatusCodeVariable "code" -SkipHttpErrorCheck
if ($code -ne 200) { Write-Error "HTTP $code" }
```

## SecureString：保護密碼

**絕對不要** 把密碼明文寫在腳本裡。用 `SecureString`：

```powershell
# 互動式輸入（最安全）
$pass = Read-Host "密碼" -AsSecureString

# 從加密檔案讀（只有同一個使用者在同一台機器才能解密）
$encrypted = "76492d1116743f0..."   # 用下面方式產生
$securePass = $encrypted | ConvertTo-SecureString

# 儲存加密字串到檔案
$plainText = "MySecretPassword"
$encrypted = $plainText | ConvertTo-SecureString -AsPlainText -Force | ConvertFrom-SecureString
$encrypted | Out-File C:\Secure\api_token.enc

# 下次讀取
$secureToken = Get-Content C:\Secure\api_token.enc | ConvertTo-SecureString

# 轉回明文（只在需要傳給 API 時才這樣做）
$plain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
)
```

## Get-Credential：標準化憑證輸入

```powershell
# 彈出 Windows 憑證對話框（互動腳本用）
$cred = Get-Credential -Message "輸入 API 憑證"
$cred.UserName
$cred.GetNetworkCredential().Password   # 取回明文（避免，只在必要時用）
```

## ExecutionPolicy 說明

ExecutionPolicy 是**安全機制**，不是加密保護——懂 PS 的人可以繞過它。目的是防止不知情的使用者意外執行惡意腳本。

| Policy | 說明 |
|--------|------|
| `Restricted`（預設） | 完全不執行腳本 |
| `AllSigned` | 只執行有受信任簽署的腳本 |
| `RemoteSigned` | 本機腳本不需簽署，下載的腳本需要 |
| `Unrestricted` | 全部執行 |
| `Bypass` | 完全跳過（CI/CD pipeline 常用）|

```powershell
Get-ExecutionPolicy -List    # 查看各 Scope 的設定

# 設定（優先順序：Process > CurrentUser > LocalMachine）
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## 腳本簽署（概念）

企業環境可能要求所有腳本都用程式碼簽署憑證（Code Signing Certificate）簽署：

```powershell
# 取得簽署憑證（需要已有 Code Signing 憑證）
$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Select-Object -First 1

# 對腳本簽署
Set-AuthenticodeSignature -FilePath .\myscript.ps1 -Certificate $cert

# 驗證簽署
Get-AuthenticodeSignature .\myscript.ps1
```

簽署後的腳本底部會多一個 `# SIG #` 區塊，這就是數位簽名。修改腳本任何內容後，簽名失效，需要重新簽署。

## 實用的 API 整合範例

```powershell
# 從 Slack Webhook 發送通知
function Send-SlackNotification {
    param(
        [string]$WebhookUrl,
        [string]$Message,
        [string]$Channel = "#alerts"
    )

    $body = @{
        channel = $Channel
        text    = $Message
    } | ConvertTo-Json

    Invoke-RestMethod -Uri $WebhookUrl -Method Post -Body $body -ContentType "application/json"
}

# 查詢天氣 API
function Get-Weather {
    param([string]$City)
    $result = Invoke-RestMethod "https://wttr.in/${City}?format=j1"
    $current = $result.current_condition[0]
    "溫度：$($current.temp_C)°C  天氣：$($current.weatherDesc[0].value)"
}
```

## 動手練習

```powershell
# 用公開 API 練習（不需要認證）

# 1. 取得 GitHub 使用者資訊
function Get-GitHubUser {
    param([string]$Username)
    $data = Invoke-RestMethod -Uri "https://api.github.com/users/$Username"
    [PSCustomObject]@{
        Login     = $data.login
        Name      = $data.name
        Company   = $data.company
        Repos     = $data.public_repos
        Followers = $data.followers
        CreatedAt = $data.created_at
    }
}

Get-GitHubUser -Username "microsoft"
Get-GitHubUser -Username "torvalds"

# 2. 測試 SecureString 加密流程
$secret = "MySuperSecret"
$encrypted = $secret | ConvertTo-SecureString -AsPlainText -Force | ConvertFrom-SecureString
$encrypted   # 一堆亂碼

# 還原
$restored = $encrypted | ConvertTo-SecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($restored))
$plain   # 應該等於 "MySuperSecret"
```

## 自我檢核

- [ ] 理解 `Invoke-RestMethod` 和 `Invoke-WebRequest` 的差異
- [ ] 知道用 `ConvertFrom-SecureString` 把加密字串存到檔案，下次用 `ConvertTo-SecureString` 讀回
- [ ] 理解 ExecutionPolicy 是「防意外」不是「防攻擊」
- [ ] 能對腳本做錯誤處理，捕捉 HTTP 4xx/5xx 錯誤

→ [Final Project：系統維運自動化套件](./final-project-sysops-toolkit.md)
