# Ch 16 — OAuth 2.0 實戰：Credentials 管理與 Token 刷新

> 目標：理解 OAuth 2.0 的流程，能在 n8n 設定任意 OAuth2 服務的 credential，並知道 Token 自動刷新的機制。

## 為什麼 OAuth 2.0 這麼麻煩

大多數 API 用 API Key 就好了（填一個字串）。但 Google、Microsoft、GitHub、Salesforce 等服務用 OAuth 2.0，原因是安全性：

- API Key 一旦洩漏，攻擊者能做你能做的所有事
- OAuth Token 有效期短（通常 1 小時），過期自動失效
- OAuth 有權限範圍（Scope），可以只授權「讀取郵件」而不是「刪除帳號」

代價是設定麻煩。本章讓你一次看懂。

---

## OAuth 2.0 完整流程

```
1. 你的 n8n (Client)                 Google (Authorization Server)
        │                                      │
        │  1. 把使用者導到 Google 授權頁面        │
        │  ──────────────────────────────────▶ │
        │                                      │
        │  2. 使用者登入並同意授權                │
        │  ◀────────────────────────────────── │
        │                                      │
        │  3. Google 給一個短暫的 Auth Code      │
        │  ◀────────────────────────────────── │
        │                                      │
        │  4. 用 Auth Code + Client Secret 換 Token │
        │  ──────────────────────────────────▶ │
        │                                      │
        │  5. 收到 Access Token + Refresh Token │
        │  ◀────────────────────────────────── │
        │                                      │
        │  6. 用 Access Token 呼叫 API           │
        │  ──────────────────────────────────▶ Google API
```

關鍵概念：

| 術語 | 說明 |
|---|---|
| Client ID | 你的應用程式的身份識別碼 |
| Client Secret | 你的應用程式的密碼（不能洩漏）|
| Auth Code | 短暫的一次性授權碼（幾分鐘過期）|
| Access Token | 實際呼叫 API 用的 Token（通常 1 小時過期）|
| Refresh Token | 用來換新 Access Token（長效，幾個月到永久）|

---

## n8n 如何處理 Token 刷新

n8n 幫你管理 Token 生命週期：

1. 呼叫 API 前，檢查 Access Token 是否快過期
2. 若快過期，用 Refresh Token 自動換新的 Access Token
3. 存回 credential，繼續呼叫 API

你**不需要**手動刷新 Token。但 Refresh Token 本身也可能過期或被撤銷（使用者重設密碼、Google 安全設定），這時你需要重新走一遍 OAuth 授權流程。

---

## 設定 Google 服務的 OAuth2 Credential

以 Google Calendar 為例（Sheets、Gmail、Drive 流程一樣）：

### 步驟 1：Google Cloud Console

1. 打開 https://console.cloud.google.com
2. 建立新 Project（或選現有的）
3. APIs & Services → Enabled APIs → 搜尋「Google Calendar API」→ Enable

### 步驟 2：OAuth 同意畫面

1. APIs & Services → OAuth consent screen
2. User Type：External（你的帳號用）/ Internal（G Suite 組織）
3. 填應用程式名稱、email
4. Scopes：新增你需要的（如 `calendar.events`）
5. Test users：加入你自己的 email

### 步驟 3：建立 OAuth 2.0 Client

1. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
2. Application type：Web application
3. Authorized redirect URIs：填入 n8n 提示的 URL（格式：`http://localhost:5678/rest/oauth2-credential/callback`）
4. 取得 Client ID 和 Client Secret

### 步驟 4：n8n 建立 Credential

1. n8n → Credentials → New → Google Calendar OAuth2
2. 填入 Client ID 和 Client Secret
3. 點「Sign in with Google」→ 你的 Google 帳號授權
4. 完成後 credential 狀態變綠

---

## 設定自訂 OAuth2 服務

如果你要接一個 n8n 沒有內建 node 的服務（但它支援 OAuth 2.0），用 **Generic OAuth2 Credential**：

```
Grant Type:        Authorization Code
Authorization URL: https://provider.com/oauth/authorize
Access Token URL:  https://provider.com/oauth/token
Client ID:         your-client-id
Client Secret:     your-client-secret
Scope:             read write
Auth URI Query Parameters:
  response_type: code
```

填完後點授權，n8n 會帶你走完 OAuth 流程。取得 token 後，在 HTTP Request node 選這個 credential，它會自動帶 Bearer Token。

---

## Credential 管理最佳實踐

**命名規範**：credential 名稱加上用途，方便識別：

```
Google Sheets - 工作報表 (n8n service account)
Telegram Bot - 警報機器人
Stripe API - 生產環境
```

**Credential 共用**：多個 workflow 可以共用同一個 credential。刪除 credential 前先確認沒有 workflow 在用。

**Secret 安全**：n8n 的 credential 資料在資料庫裡是加密儲存的（AES-256）。但資料庫本身要保護好，不能讓未授權的人存取 `~/.n8n/` 目錄或 Postgres 的 n8n 資料庫。

---

## 常見踩雷

**「invalid_grant」錯誤**

Refresh Token 失效了，通常是：
- Google 超過 7 天沒用（測試模式 app 的限制）
- 使用者在 Google 帳號管理頁面撤銷了授權
- 同一個 Google App 發了超過 50 個 refresh token（有上限）

解法：在 n8n 重新授權 credential。

**「redirect_uri_mismatch」**

OAuth 設定裡的 Redirect URI 和 n8n 給的不一樣。回 Google Cloud Console 確認有加對 URI。

---

## 自我檢核

- [ ] 能說出 Access Token 和 Refresh Token 的差異
- [ ] 能在 Google Cloud Console 建立 OAuth2 Client 並設定 Redirect URI
- [ ] 能在 n8n 完成 Google 服務的 OAuth2 授權
- [ ] 知道 invalid_grant 錯誤的常見原因和解法

Part 3 結束。做練習 B 把服務整合能力都用上。

→ [練習 B：多服務 Pipeline](./practice-b-multi-service-pipeline.md)
