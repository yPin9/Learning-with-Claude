# Ch 22 — 環境變數、Secrets、使用者權限管理

> 目標：正確管理 n8n 的敏感設定，設定多使用者存取控制，讓不同角色只能看到他們應該看到的東西。

## n8n 的設定分層

```
環境變數 (.env / docker env)    ← 系統級設定，啟動時讀取
        │
n8n Credential 系統              ← API key / OAuth token，加密存在 DB
        │
Workflow Settings                ← 每個 workflow 的個別設定
        │
使用者角色                        ← 誰能做什麼
```

---

## 環境變數：重要設定一覽

除了 Ch 21 用的基本設定，還有幾個值得知道的：

```bash
# 執行歷史保留
EXECUTIONS_DATA_MAX_AGE=168          # 保留 7 天（小時）
EXECUTIONS_DATA_PRUNE=true           # 自動清理舊 execution
EXECUTIONS_DATA_PRUNE_MAX_COUNT=5000 # 最多保留 5000 筆

# 並發限制
N8N_DEFAULT_CONCURRENCY_LIMIT=10    # 最多同時 10 個 workflow 執行
QUEUE_WORKER_CONCURRENCY=5          # Queue 模式下的 worker 並發數

# 停用 Telemetry（不想把使用資料傳給 n8n）
N8N_DIAGNOSTICS_ENABLED=false
N8N_VERSION_NOTIFICATIONS_ENABLED=false

# 限制 Code Node 可用的模組（安全性）
NODE_FUNCTION_ALLOW_BUILTIN=crypto,url,path
NODE_FUNCTION_ALLOW_EXTERNAL=lodash,luxon

# 外部 Hook（進階，Ch 外補充）
N8N_EXTERNAL_HOOK_FILES=/home/node/.n8n/hooks.js
```

---

## Credential 的加密機制

n8n 所有 Credential（API key、OAuth token、資料庫密碼）都用 `N8N_ENCRYPTION_KEY` 加密後存在資料庫。

**這個 key 是你最重要的秘密**：

- 丟了就無法解密 credential，所有服務整合全部失效
- 洩露了就等於洩露了你所有的 API key
- **一定要備份，不能和資料庫備份放在同一個地方**

```bash
# 把 key 存到密碼管理工具（1Password、Bitwarden）
# 不要只放在 .env 檔案裡
```

如果你要遷移 n8n 到新主機：

1. 複製資料庫 dump
2. 複製 `N8N_ENCRYPTION_KEY`（**必須一模一樣**）
3. 在新主機用同樣的 key 啟動，credential 才能正常解密

---

## 在 workflow 裡使用環境變數

不要把 API key 硬寫在 Code Node 裡：

```javascript
// 錯的
const apiKey = 'sk-1234567890abcdef';

// 對的：用環境變數
const apiKey = $env.MY_API_KEY;
```

先在 .env 加：

```bash
MY_API_KEY=sk-1234567890abcdef
```

然後在環境變數設定裡讓 n8n 能讀到這個 key（Docker Compose 的 environment 裡加）：

```yaml
environment:
  MY_API_KEY: ${MY_API_KEY}
```

---

## 使用者管理（n8n v1.0+）

n8n 1.0 之後有完整的多使用者管理，不再只有 Basic Auth 的單帳號模式。

### 使用者角色

| 角色 | 能做什麼 |
|---|---|
| **Owner** | 全部。管理使用者、設定、所有 workflow |
| **Admin** | 管理使用者（除了刪除 Owner）、所有 workflow |
| **Member** | 只能看到/編輯分享給他的 workflow |

### 邀請新使用者

n8n 介面 → Settings → Users → Invite User

填 email，選角色，對方收到邀請信後設定密碼。

如果你的 n8n 沒有 SMTP 設定，邀請連結不會用 email 發送，要手動複製連結給對方：

```bash
N8N_EMAIL_MODE=smtp
N8N_SMTP_HOST=smtp.gmail.com
N8N_SMTP_PORT=587
N8N_SMTP_USER=your@gmail.com
N8N_SMTP_PASS=your-app-password
N8N_SMTP_SENDER=no-reply@yourdomain.com
```

### Workflow 分享

每個 workflow 可以設定分享給特定成員（Settings → Share），未分享的 workflow 其他 Member 看不到。

---

## Credential 存取控制

同樣，Credential 預設只有建立者能用。在 Credential 的 Settings → Share，可以分享給其他使用者。

**最佳實踐**：建一個 `Service Accounts` 類的使用者，專門用來建共用 Credential（如 Postgres、Telegram Bot），然後分享給需要的成員。個人的 OAuth Credential 不要共用。

---

## 限制外部存取

生產環境不要讓 n8n 直接暴露在公網（已經在 Ch 21 的 Nginx 後面了），額外加上 IP 白名單：

```nginx
# nginx.conf：只允許特定 IP 存取 n8n 管理介面
location /rest/ {
    allow 1.2.3.4;   # 你的辦公室 IP
    deny all;
}

location /webhook/ {
    allow all;        # Webhook 端點對全部開放
}
```

---

## 自我檢核

- [ ] 知道 `N8N_ENCRYPTION_KEY` 為什麼要備份且不能洩漏
- [ ] 能用環境變數在 Code Node 裡存取 API key（而不是硬寫）
- [ ] 知道 n8n 三種使用者角色的權限差異
- [ ] 能設定 Workflow 和 Credential 的分享

→ [Ch 23 監控、日誌、備份與版本升級](./23-monitoring-backup.md)
