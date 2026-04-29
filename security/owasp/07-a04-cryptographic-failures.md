# Ch 7 — A04 Cryptographic Failures

> 目標：搞懂加密相關的常見錯誤 — 弱算法 / 明文傳輸 / 密碼存錯 / 隨機性不足。

> **2025 變動**：在 2021 是 #2，**2025 降到 #4**。降不是因為變不重要，而是 misconfig / supply chain 問題的測試命中率上升把它擠下去。本身的攻擊面跟以前一樣大。

## 「Cryptographic Failures」是什麼

OWASP 2021 把舊的「Sensitive Data Exposure」改名（2025 沿用）— 強調**根本原因是加密用錯**，不只是資料外洩結果。

包含：

- 明文傳輸 sensitive data
- 用過時 / 弱算法
- 密碼存錯（明文 / MD5）
- 隨機性不足（弱 token / session）
- 金鑰管理錯（hard code in source）

## 1. 明文傳輸

### HTTP vs HTTPS

```
client → server: POST /login
                 user=alice&password=secret123    ← 明文！
```

任何中間網路 hop 能看：ISP / Wi-Fi 同網路 / 國家防火牆。

修：**永遠 HTTPS**。

### 即使 HTTPS 也可能洩

- DNS 查詢看 domain
- SNI 看 domain（除非 ECH）
- traffic analysis（time / size）

絕對隱私要 Tor / 其他 anonymizer。一般 HTTPS 已經 99% 場景安全。

### 「混合內容」

HTTPS page 載入 HTTP image / JS → browser 警告 / block：

```html
<img src="http://example.com/logo.png">  ← 在 HTTPS page 上
```

對策：所有 resource 都 HTTPS、用 `https://` 或 protocol-relative `//`。

## 2. 弱加密算法

歷史上「**安全算法**」會過時：

| 算法 | 狀態 |
|---|---|
| DES | 死透（56 bit key） |
| 3DES | deprecated |
| MD5 | hash 死透（collision easy） |
| SHA-1 | 已死（2017 collision） |
| RC4 | 死 |
| RSA-1024 | 弱 |
| ECB mode | 永遠別用 |
| CBC + no MAC | 易 padding oracle attack |

**現代用**：

- 對稱：**AES-256-GCM** / ChaCha20-Poly1305
- 非對稱：**RSA-2048+** / Ed25519 / X25519
- Hash：**SHA-256** / SHA-3 / BLAKE2
- HMAC：HMAC-SHA-256
- Password hash：**bcrypt** / scrypt / Argon2

「**老 system 不要 maintain 老算法**」，遷移到現代 cipher suite。

## 3. 密碼存錯

### 最壞：明文

```sql
SELECT * FROM users WHERE username='alice' AND password='secret123';
```

DB 被攻擊 → 全 user 密碼洩 → user 在其他 site 也常用同密碼 → 多 site 連環 breach。

### 次壞：未加 salt 的 hash

```python
hash = sha256(password)
```

問題：

- 攻擊者用 **rainbow table**（預先算好所有常見密碼的 hash）→ 秒查
- 同密碼 → 同 hash → 攻擊者看出哪些 user 用同密碼

### 正確：salted + slow hash

```python
import bcrypt
hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
# bcrypt 自動加 random salt + 慢 hash
```

bcrypt / scrypt / Argon2：

- **Salt**：每密碼隨機 salt → rainbow table 失效
- **Slow**：故意慢（10ms+ per hash）→ brute force 慢
- **Adaptive**：cost factor 可調，硬體變強就調高

**現代密碼存儲必用 bcrypt / Argon2**。SHA-256 不夠（太快）。

## 4. 隨機性不足

### 弱隨機 = 可預測 token

```python
import random
token = random.randint(0, 1000000)   # 偽隨機，可預測！
```

`random` module 是 PRNG，攻擊者知道 seed 能算出整個序列。

### 用 cryptographic random

```python
import secrets
token = secrets.token_hex(32)        # 安全
session_id = secrets.token_urlsafe(32)
```

不同語言對應：

- Python: `secrets`
- Node: `crypto.randomBytes`
- Java: `SecureRandom`
- Go: `crypto/rand`
- C: `/dev/urandom` 或 OS RNG

**永遠用 cryptographic random for security tokens**。

## 5. 金鑰管理

### Hard-code in source

```python
SECRET_KEY = "my-super-secret-key-2024"   # 災難！
```

任何 GitHub / 同事 / leaked source → key 洩漏。

GitHub 公開 repo 有 secret scanner，AWS / GitHub key 一推就被通報。

### 環境變數

```python
import os
SECRET_KEY = os.environ['SECRET_KEY']
```

至少不在 source。但如果 env 從 unencrypted file 讀（如 `.env`）→ 進 git 又洩。

`.env` 加 `.gitignore`：

```
# .gitignore
.env
*.pem
*.key
```

### Secret manager

production 用：

- AWS Secrets Manager / Parameter Store
- HashiCorp Vault
- Azure Key Vault
- Google Secret Manager

key rotation / audit / access control 集中管理。

## 6. JWT 弱簽

```
header: {"alg": "HS256"}
payload: {"user": "alice", "role": "user"}
signature: HMAC-SHA256(header.payload, secret)
```

**HS256 secret 弱** → brute force：

```bash
# john / hashcat
hashcat -a 0 -m 16500 jwt.txt rockyou.txt
```

短 / 字典 secret 幾秒破。

修：

- **secret > 32 byte 隨機**（用 `secrets.token_urlsafe(64)`）
- 改用 **RS256**（asymmetric，私鑰簽 / 公鑰驗）

## 7. JWT alg=none 攻擊

如果 server 接受 `alg: none`：

```
原 JWT: eyJhbGc.eyJzdWIifQ.SflKxw   (HS256 簽過)
攻擊者: 改 header 為 {"alg":"none"}
        改 payload 為 {"user":"admin"}
        signature 留空
新 JWT: eyJhbGciOiJub25lIn0.eyJ1c2VyIjoiYWRtaW4ifQ.
```

server 看 alg=none → 不驗 signature → 接受 → 攻擊者變 admin。

**library 該 reject alg=none**，但歷史上很多 library 預設接受。

## 真實案例：Adobe（2013）

Adobe 1.5 億用戶資料外洩：

- 密碼用 **3DES ECB** 加密（不是 hash！）
- ECB mode 同密碼 → 同 ciphertext → 攻擊者用 frequency analysis 解
- 加上 password hint 欄位（明文）→ 反推

**全部用戶密碼幾天內被解出**。

教訓：

- 密碼**用 hash 不用加密**（不需要可逆）
- ECB **永遠別用**
- password hint 是壞設計

## 動手練習

**1. 看 site 的 SSL config**

```bash
# Mozilla SSL Test
nmap --script ssl-enum-ciphers -p 443 example.com

# 線上：https://www.ssllabs.com/ssltest/
```

看 cipher / TLS 版本 / certificate。希望 A 評分。

**2. 寫 / 修 vulnerable code**

```python
# vulnerable.py — 用 SHA-256 存密碼（弱）
import hashlib

def hash_password_bad(password):
    return hashlib.sha256(password.encode()).hexdigest()

# fix.py — 用 bcrypt
import bcrypt

def hash_password_good(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed)
```

對比：

- `hash_password_bad("hello")` 跑 1 us
- `hash_password_good("hello")` 跑 100ms

慢 100,000x — 對 brute force 100,000x 困難。

**3. 破 JWT secret**

```bash
# 自己生個弱 secret 的 JWT
python3 -c '
import jwt
token = jwt.encode({"user": "alice"}, "secret123", algorithm="HS256")
print(token)
'

# hashcat 破
echo "<token>" > jwt.txt
hashcat -a 0 -m 16500 jwt.txt rockyou.txt
```

**4. Juice Shop crypto challenges**

- "Forged Coupon"（弱 hash）
- "JWT Tampering"
- "Login Admin"（弱密碼 + SQL injection）

**5. 看 git 歷史是否有 secret**

```bash
# 用 truffleHog
trufflehog filesystem . --no-update
trufflehog git https://github.com/your/repo
```

掃自己舊 repo 是否 commit 過 secret。

## 自我檢核

- [ ] 講得出明文傳輸的風險
- [ ] 知道 MD5 / SHA-1 / DES / RC4 為什麼不安全
- [ ] 密碼用 bcrypt / Argon2 不是 SHA-256
- [ ] 知道 secrets module vs random module 差別
- [ ] JWT alg=none 攻擊原理
- [ ] 對自己 site 跑過 SSL test

下一章進注入大宗 — SQL Injection（2025 把 SQLi / XSS / 各類注入合併到 A05）。

→ [Ch 8 A05 SQL Injection 深入](./08-a05-sql-injection.md)
