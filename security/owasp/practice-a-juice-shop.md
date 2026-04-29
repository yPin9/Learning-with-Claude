# 練習 A — 攻 OWASP Juice Shop

> 目標：用 Ch 4-19 學的 OWASP 知識 + 工具，對 OWASP Juice Shop 完成 50% 以上 challenges，寫 attack chain report。

> 本練習所有 OWASP 編號採 **2025 版**。Juice Shop 的 challenge 內部 tag 仍為 2021 版（畫面上顯示 A01:2021 / A03:2021 等），請對照 README 的 2021→2025 對照表理解。

## 任務規格

| # | 任務 | 驗收 |
|---|---|---|
| 1 | Juice Shop 跑起來 | http://localhost:3000 訪問 OK |
| 2 | 完成 ≥ 50% challenges (>50/100+) | Score Board 顯示 |
| 3 | 至少完成每難度 (1-6 stars) 的 challenge | 各難度都接觸 |
| 4 | 寫 attack chain：5+ 個 challenge 的詳細解法 | report 含 step / payload / 截圖 / OWASP 對應 |
| 5 | 用 ≥ 3 個工具 (Burp / sqlmap / nuclei / ffuf 等) | report 顯示工具用過 |

## Juice Shop 簡介

OWASP 官方 vulnerable web app：

- Modern SPA (Angular + Express + SQLite)
- **100+ challenges** 對應 OWASP Top 10、API Top 10、商業邏輯漏洞
- 每 challenge 有 difficulty (1-6 stars)
- Score Board 追蹤進度

## 啟動

```bash
docker run -d -p 3000:3000 --name juice-shop bkimminich/juice-shop

# 訪問
firefox http://localhost:3000
```

## Score Board

第一個 challenge：**找到 Score Board**！

提示：用 dev tools / Burp / 直接猜 URL（`/score-board`）。

完成後看到所有 challenges 列表。

## Challenge 範例 + 解法概要

### 簡單級 (1-2 stars)

#### "DOM XSS" (1 star, A05 / 2021:A03)

URL: 訪問 `/#/search?q=<iframe src="javascript:alert(`xss`)">`

#### "Login Admin" (2 stars, A05 / 2021:A03)

Email: `' OR 1=1--`  
Password: random

SQL injection bypass auth。

#### "View Basket" (2 stars, A01)

Login 後 → Burp 看 GET `/rest/basket/<id>` → 改 ID 為其他用戶。

### 中等級 (3-4 stars)

#### "Reset Jim's Password" (4 stars, A07)

研究 Jim 的 security question (找 review 中提示)。Reset password 流程：給對 security question answer → 改密碼。

#### "Database Schema" (3 stars, A05 / 2021:A03)

product search 含 SQL injection。用 UNION SELECT 拿 schema：

```
qwert')) UNION SELECT sql,2,3,4,5,6,7,8,9 FROM sqlite_master--
```

### 困難級 (5-6 stars)

#### "Forged JWT" (6 stars, A04 + A07 / 2021:A02+A07)

JWT secret 弱 / alg=none：

```bash
python3 jwt_tool.py <jwt> -X a   # alg=none attack
```

或：

```bash
hashcat -a 0 -m 16500 jwt.txt rockyou.txt
```

#### "RCE via deserialization" (6 stars, A08)

Juice Shop 有 endpoint deserialize untrusted input → exploit get RCE。

## 系統化 approach

### 1. Recon

```bash
# 看 site 結構
ffuf -u http://localhost:3000/FUZZ -w common.txt

# 找 hidden API
nuclei -u http://localhost:3000

# subdomain (Juice Shop 沒，但概念)
```

### 2. Authentication

- 註冊普通 user
- 登入 → 拿 JWT
- 用 Burp 看 JWT 結構

### 3. 逐 OWASP 試

對應 README 對照表：

| OWASP 2025 | (2021) | Juice Shop challenge |
|---|---|---|
| A01 Broken Access Control | A01 | View Basket / Repeat Charges / Forged Feedback |
| A01（含 SSRF） | (2021:A10) | SSRF (profile image 上傳收 URL) |
| A02 Misconfiguration | (2021:A05) | Error Handling / Outdated Whitelist |
| A03 Supply Chain | (2021:A06) | Vulnerable Library |
| A04 Cryptographic Failures | (2021:A02) | Forged JWT / Bonus Payload |
| A05 Injection (SQL) | (2021:A03) | Login Admin / Login Bender / Database Schema |
| A05 Injection (XSS) | (2021:A03) | DOM XSS / Reflected XSS / Persisted XSS |
| A06 Insecure Design | (2021:A04) | Forged Coupon / Negative Score Cheat |
| A07 Authentication Failures | A07 | Login Admin / MFA / Password Strength |
| A08 Integrity Failures | A08 | Deserialization / Login Backdoor |
| A09 Logging & Alerting Failures | (2021:A09) | Login Backdoor（沒 log it） |
| A10 Mishandling Exceptions | (新類別) | Error Handling 觸發 stack trace 外洩 / Forced Endpoint exception |

每組玩 1-2 個。

## Attack chain 寫法

每個 challenge 寫：

```markdown
### Challenge: Login Admin

**Difficulty**: 2 stars  
**OWASP**: A05 Injection (SQL) [2021:A03]

**Steps**:
1. 訪問 /login
2. 開 Burp 攔 POST /rest/user/login
3. Body: `{"email": "' OR 1=1--", "password": "x"}`
4. Send → 200 OK + JWT for admin user
5. 拿到 JWT → 設 cookie / Authorization header → admin 登入

**Payload**:
```
{"email": "' OR 1=1--", "password": "x"}
```

**Why it works**: server 拼 SQL: 
`SELECT * FROM users WHERE email='' OR 1=1--' AND ...`
`--` comment 掉 AND，return 第一個 user (admin)

**Tool used**: Burp Repeater

**Defense**: 用 prepared statement
```python
cursor.execute("SELECT * FROM users WHERE email=? AND password=?", (email, hashed))
```
```

寫 5+ 個。每個 100-300 字。

## 完整參考

**做完 50% 自己再來看**！

<details>
<summary>常見挑戰提示</summary>

### Score Board
- 訪問 `/#/score-board`

### Login Admin
- SQL injection: `email = ' OR 1=1--`

### Login Bender
- 知 Bender 的 email (`bender@juice-sh.op`)
- SQL: `email = bender@juice-sh.op'--`

### Reset Jim's Password
- Security Q answer: "Samuel"

### Forged Coupon
- coupon 是 base64 over time-based generator
- 倒推算法 forge

### View Another User's Cart
- Burp 改 basket ID

### DOM XSS
- URL: `/#/search?q=<iframe src="javascript:alert(`xss`)">`

### Forged JWT
- alg=none attack 或 weak secret brute

### SSRF
- profile image upload 接受 URL → 用 internal URL

</details>

## 進階挑戰

**A. 100% completion**：玩到全 challenge 完成（可能要 20-50 小時）

**B. Speed run**：看 30 分鐘能完成幾個（限制工具）

**C. 寫自動化 script**：用 Python + requests 自動 exploit 5+ challenges

**D. CTF style write-up**：每 challenge 寫成 CTF writeup（步驟 + 截圖 + 教育價值）

## 自我檢核

- [ ] Juice Shop 完成 ≥ 50% challenges
- [ ] 寫 attack chain ≥ 5 個 challenges
- [ ] 用過 Burp / sqlmap / nuclei / ffuf 至少 3 個
- [ ] 對每個 OWASP Top 10 都試過至少 1 個
- [ ] 知道每個 challenge 對應的 OWASP
- [ ] 有 1 challenge 卡住但研究後解開（看 hints / writeup OK）

下個 Part 進 OWASP API Security Top 10。

→ [Ch 20 API 安全 + API1-3](./20-api-top10-part1.md)
