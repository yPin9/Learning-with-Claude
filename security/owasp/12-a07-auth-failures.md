# Ch 12 — A07 Authentication Failures

> 目標：搞懂認證系統的常見漏洞 — session fixation、brute force、MFA bypass、JWT 弱點。

> **2025 變動**：2021 叫「Identification & Authentication Failures」，**2025 縮短為「Authentication Failures」**。範圍實質不變。

## A07 包含什麼

「**確認 user 是誰**」這事的所有漏洞：

- 弱密碼政策
- session 管理錯
- brute force 沒擋
- MFA 設計錯
- JWT 漏洞
- credential stuffing
- session fixation / hijacking

「**Identification**」 = 你聲稱誰；「**Authentication**」 = 證明你是。

## 1. 弱密碼政策

```
✗ 允許 "123456" / "password"
✗ 沒 password length minimum
✗ 沒擋常見密碼字典
```

修：

- 最少 12 chars（NIST 2024 建議）
- 不擋字典密碼（NIST 不再建議「**must contain symbol**」這種規則）
- 鼓勵 passphrase
- 提供 password manager 友善
- 後端不限制最大長度

## 2. Brute force / Credential stuffing

### Brute force

對單一 user 試很多密碼。

修：

- 5-10 次 fail 後 lockout
- exponential backoff
- CAPTCHA
- IP rate limit

### Credential stuffing

用「**已洩漏的 user/pass 組合**」（從別站 leak）試 your site。

數據：每天有 billion 級的 credential stuffing attempts。

修：

- 接 HaveIBeenPwned API：user 設密碼時 check 是否在 leak DB
- 偵測「**單 IP 試很多 user**」（vs 單 user 多密碼）
- MFA

## 3. Session 管理

### Session fixation

attacker 給 victim 一個固定的 session ID，victim login 後 attacker 用同 ID hijack：

```
1. attacker 訪問 login page → 拿到 session=abc123
2. attacker 寄 link 給 victim：https://target.com/?session=abc123
3. victim 點 → cookie 設成 abc123 → login
4. attacker 用 abc123 → 已 login 為 victim
```

修：**login 後產生新 session ID**，不沿用 pre-login 的：

```python
@app.route('/login', methods=['POST'])
def login():
    if check_password(...):
        session.clear()
        session.regenerate_id()    # ← 重要
        session['user_id'] = user.id
```

### Session hijacking

直接偷 cookie。途徑：

- XSS → `document.cookie`
- 中間人（HTTP）
- malware on user device

修：

- HTTPS（Secure flag）
- HttpOnly（防 XSS 偷）
- SameSite（防 CSRF）
- 短 expiration
- 重要操作要求 re-auth

### Session 沒 expire

```
user 登入 → 無限期 session
user 公共電腦離開沒 logout
下個 user 開 browser → 還在 victim 帳號
```

修：

- absolute timeout（最長 N 天）
- inactivity timeout（閒 30 分鐘自動 logout）
- 顯示 active sessions，user 可 revoke

## 4. MFA（Multi-Factor Authentication）

### 強 vs 弱 MFA

| MFA 類型 | 強度 |
|---|---|
| **FIDO2 / WebAuthn** | 最強（hardware key） |
| **TOTP**（Google Authenticator） | 強 |
| **App push**（Duo） | 強，但 push fatigue 攻擊有效 |
| **SMS** | 弱（SIM swap、SS7 攻擊） |
| **Email link** | 中（看 email account 安全） |

production 該用 TOTP 起跳。**SMS 是上世紀標準**。

### MFA bypass

#### 1. Recovery flow 沒 MFA

「**忘記密碼**」流程不需要 MFA → bypass MFA：

```
attacker 知 victim email → 觸發 password reset → 用 email 改密碼 → 登入（沒 MFA 觸發）
```

修：reset 後 MFA 仍要驗。

#### 2. MFA 不 enforce 在所有 endpoint

```
/dashboard → 要 MFA
/api/users/me → 沒 check MFA → bypass
```

修：global MFA check middleware。

#### 3. MFA token 重複使用

某些 broken impl 接受同 TOTP code 多次。修：嚴格 nonce。

#### 4. Push fatigue

attacker 每分鐘 trigger MFA push → victim 煩了 → 不小心 approve。

修：limit push frequency；要求 push UI 顯示登入地點 / IP。

#### 5. MFA 用 vulnerable channel

SMS → SIM swap → 攻擊者拿 SMS。

修：避用 SMS。

## 5. JWT 漏洞

### alg=none

回 Ch 5。如果 server 沒 verify signature → 改 payload 即可變身。

### Weak HS256 secret

```bash
# 用 hashcat 破
hashcat -a 0 -m 16500 jwt.txt rockyou.txt
```

弱 secret（短、字典） → 秒破。

### Algorithm confusion

server 接受 RS256（asymmetric） + HS256（symmetric）：

```python
# vulnerable
def verify_token(token):
    return jwt.decode(token, public_key)   # 沒指定 algorithm
```

attacker：

- 拿到 server public key（通常公開）
- 造 HS256 JWT，用 public key 當 secret 簽
- server 看 alg=HS256 → 用 public key 當 secret verify → ✓

修：明確指定 algorithm：

```python
jwt.decode(token, public_key, algorithms=['RS256'])
```

### JWT 沒 revoke 機制

JWT stateless → server 不存 session → **發出去後不能撤回**。

如果 user 改密碼 / 帳號被偷 → 舊 JWT 還能用到 expiration。

修：

- 短 expire (15 分鐘)
- 用 refresh token 機制
- 維護 revocation list（部分 stateless 退讓）

## 6. OAuth / SSO 漏洞

### Open redirect 在 OAuth

```
GET /oauth/authorize?client_id=...&redirect_uri=https://evil.com/cb
```

如果 server 沒嚴格驗 `redirect_uri` → attacker 拿到 OAuth code → 換 access token。

修：嚴格白名單 `redirect_uri`，不允許 wildcard subdomain。

### CSRF on OAuth callback

OAuth callback 沒 state parameter → attacker 用自己 OAuth flow 結合 victim session → 把 attacker account 連到 victim app。

修：強制 `state` parameter，檢查匹配。

## 真實案例：Twitter（2022）

兩個 0-day：

- **CVE-2022-23305**（Log4j）
- 加上 OAuth misconfig：API endpoint 接受 phone/email 直接 query 對應 user → bulk lookup → 540 萬 user 資料外洩

教訓：認證系統一個漏洞 = bulk leak 整個 user base。

## 動手練習

**1. Brute force lab**

對 DVWA login，用 hydra brute force：

```bash
hydra -l admin -P /usr/share/wordlists/rockyou.txt \
  192.168.1.10 http-post-form \
  "/login.php:username=^USER^&password=^PASS^:Login failed"
```

**2. JWT 攻擊**

```bash
# 拿 Juice Shop / 自己 app 的 JWT
# 解碼 header / payload
echo "<jwt>" | cut -d. -f1 | base64 -d
echo "<jwt>" | cut -d. -f2 | base64 -d

# 試 alg=none
# 用 jwt_tool（GitHub jhwx/jwt_tool）
python3 jwt_tool.py <jwt> -X a   # alg=none attack

# 試 brute force secret
hashcat -m 16500 jwt.txt rockyou.txt
```

**3. Session fixation 重現**

寫個 vulnerable Flask（pre-login session ID 不重產），用 Burp 攻。

**4. MFA bypass scenarios**

對自己用的 site 思考：

- Reset password 後是否強制 MFA？
- 改 email 是否需要 MFA？
- 全部 endpoint 都 check MFA 嗎？

**5. Juice Shop auth challenges**

- "Login Admin"
- "Login Bender"
- "Login Jim"
- "Multi-Factor Authentication" challenge

## 自我檢核

- [ ] 知道 brute force vs credential stuffing 差別
- [ ] Session fixation 攻擊原理
- [ ] MFA 強弱排序（FIDO2 > TOTP > SMS）
- [ ] 5+ 種 MFA bypass
- [ ] JWT 3 大攻擊（alg=none / 弱 secret / algo confusion）
- [ ] 用 hydra / jwt_tool 練過
- [ ] Juice Shop auth challenges 完成

下一章看 A08 Software & Data Integrity。

→ [Ch 13 A08 Software & Data Integrity Failures](./13-a08-integrity-failures.md)
