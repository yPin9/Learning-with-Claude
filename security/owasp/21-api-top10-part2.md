# Ch 21 — API 4-7

> 目標：API4 Resource Consumption / API5 Function-Level Auth / API6 Sensitive Business Flow / API7 SSRF。

## API4: Unrestricted Resource Consumption

「**沒限制 API call 量 / 大小 / 複雜度**」 → DoS / 高額帳單。

### 攻擊面

#### 1. 沒 rate limit

```bash
# attacker 跑 100 萬 request
for i in {1..1000000}; do
  curl /api/expensive-endpoint &
done
```

server CPU / DB 耗盡。

#### 2. 大 body / file upload

```bash
curl -X POST /api/upload -F "file=@huge.bin"   # 10 GB file
```

server 記憶體 OOM。

#### 3. GraphQL query 複雜度爆炸

```graphql
query {
  user {
    friends {
      friends {
        friends {
          friends {
            ...
```

depth 10 → 每 user 100 friends → 100^10 = 10^20 records query。

#### 4. expensive endpoint

```
GET /api/search?q=*&limit=1000000
```

DB 跑全 scan。

### 防禦

#### 1. Rate limit

per-IP, per-user, per-endpoint：

```python
# Flask-Limiter
@app.route('/api/...')
@limiter.limit("10 per minute")
def endpoint():
    ...
```

進階：sliding window / token bucket。

#### 2. Request size limit

```python
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1 MB
```

或 nginx 層：

```nginx
client_max_body_size 1M;
```

#### 3. Query complexity / pagination

```
# 強制 pagination
limit max 100
```

GraphQL：

- depth limit
- complexity scoring（每 field 算 cost，總 cost 限制）

#### 4. Timeout

```python
@timeout(seconds=5)
def expensive_query():
    ...
```

防 long-running query 卡 server。

#### 5. Cost-based throttling

對 cloud-bill-ed service：

- 每 user 每月 budget
- 超過 → degrade or block

## API5: Broken Function Level Authorization

「**user 能 call 不該 call 的 function / endpoint**」。

vs BOLA 比較：

- BOLA：**特定 object** 沒 check（user A 看 user B 的 profile）
- BFLA：**整個 function** 沒 check（user 用 admin endpoint）

### 例

```
PUT /api/users/123/role           ← admin only
DELETE /api/products/456          ← admin only
GET /api/admin/users              ← admin only
```

普通 user 直接 call → 沒 check → 過。

### 攻擊模式

普通 user JWT → call admin endpoint：

```bash
TOKEN_USER=...
curl -H "Authorization: Bearer $TOKEN_USER" -X DELETE /api/admin/users/123
# 該 403，但 server 沒 check → 200 → 災難
```

### 防禦

每個 endpoint 明確 role check：

```python
@app.route('/api/admin/users')
@require_auth
@require_role('admin')   # ← 重要
def admin_users():
    return list_all_users()
```

middleware-level enforcement：

```python
def require_role(role):
    def wrapper(f):
        @wraps(f)
        def inner(*args, **kwargs):
            if current_user.role != role:
                abort(403)
            return f(*args, **kwargs)
        return inner
    return wrapper
```

或用 framework 的 RBAC（Django permissions, Spring Security 等）。

## API6: Unrestricted Access to Sensitive Business Flows

「**業務流程**沒做 abuse 防禦」。

跟 A06 Insecure Design 重疊（2021 編號 A04），但 API 角度：

### 例

#### 1. 票務搶購

```
api/tickets/buy
```

scalper bot 1 秒 1000 次 → 真用戶買不到。

#### 2. Coupon 重複用

無 atomic check → race condition → 無限用 coupon。

#### 3. Newsletter spam

```
POST /api/newsletter/subscribe
```

attacker subscribe 別人 email → 騷擾 / spam。

#### 4. Account creation abuse

無 captcha / phone verify → 攻擊者建幾百萬假帳號。

### 防禦

#### 1. CAPTCHA

reCAPTCHA / hCaptcha 對「**人類唯一**」操作（注冊、票務）。

#### 2. Device fingerprinting

每設備發 cookie + 追蹤 → 異常 device 拒絕。

#### 3. Behavioral analysis

正常 user 點 button 1 秒一次；bot 100 次。模式偵測。

#### 4. Atomic transaction

```python
# DB level
UPDATE tickets SET buyer = %s WHERE id = %s AND buyer IS NULL
# 只 1 個 attacker 拿到
```

#### 5. Out-of-band verify

phone / email 確認，慢 attacker。

## API7: Server Side Request Forgery (SSRF)

跟 web A10 一樣（Ch 15），但 API 更常見：

API 常做：

- 抓 user 給的 URL（thumbnail generation / link preview）
- webhook
- file import
- import from URL

→ SSRF 機會多。

### 例：image thumbnail

```python
@app.route('/api/thumbnail')
def thumbnail():
    url = request.args.get('url')
    img = requests.get(url)
    return resize(img.content)
```

attacker：

```
?url=http://169.254.169.254/latest/meta-data/iam/security-credentials/
?url=http://internal-redis:6379/
?url=file:///etc/passwd
```

### 防禦

回 Ch 15：

- 白名單 URL
- block private IP
- 限 scheme（http/https only）
- 限 redirect
- network segmentation
- IMDSv2

## 一個常見誤解：「API 用 token = 安全」

**錯**。token **只解 authentication**（你是誰），**不解 authorization**（你能做什麼）。

每個 endpoint 仍需 BOLA / BFLA check。

## 一個常見誤解：「rate limit 在前端做」

**錯**。前端 rate limit 是 UX，**安全要 server 做**。

attacker 跳過前端直接 curl → 前端 limit 沒效。

## 一個常見誤解：「我的 API 沒人用 = 不需要 rate limit」

**錯**。internet-exposed API 5 分鐘內被 scanner 找到。沒 rate limit = 第一個 attacker 來就 down。

## 動手練習

**1. 寫 rate limit**

```python
from flask_limiter import Limiter

limiter = Limiter(app, key_func=lambda: request.remote_addr)

@app.route('/api/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    ...
```

測試：跑 10 個 request，第 6 個應該 429。

**2. BFLA test**

對自己 API 用「普通 user token」call admin endpoint：

```bash
curl -H "Authorization: Bearer $USER_TOKEN" /api/admin/users
# 該 403，如果 200 → BFLA
```

**3. SSRF in image API**

寫個 vulnerable thumbnail API（accept URL → download → resize）。攻：

```bash
curl 'http://localhost:5000/api/thumbnail?url=http://localhost:5000/api/admin/users'
curl 'http://localhost:5000/api/thumbnail?url=http://169.254.169.254/'
```

修：白名單 + private IP block。

**4. 業務 flow abuse**

對自己 API 思考：

- 哪個 endpoint 用 1 萬次會壞？
- 哪個能 bot 化攻擊（注冊 / 評論 / 訂單）？
- 設防禦（CAPTCHA / rate limit / device fingerprint）

**5. PortSwigger API security**

https://portswigger.net/web-security/api-testing

完整 API testing labs。

## 自我檢核

- [ ] 知道 BFLA vs BOLA 差別
- [ ] Rate limit 4+ 種策略（IP / user / endpoint / global）
- [ ] GraphQL query complexity 概念
- [ ] 業務流程濫用 5+ 種模式
- [ ] API SSRF 防禦同 web SSRF
- [ ] 寫 API 時習慣加 limit + authz check

下一章看 API8-10。

→ [Ch 22 API8-10](./22-api-top10-part3.md)
