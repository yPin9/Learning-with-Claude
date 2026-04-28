# Ch 20 — API 安全 + API1-3

> 目標：搞懂 API 安全跟 web app 的差別，學 OWASP API Security Top 10 2023 的 API1-3。

## 為什麼 API 有專屬 Top 10

API 跟 web app 攻擊面不同：

| 維度 | Web App | API |
|---|---|---|
| 受眾 | 人類 (browser) | 程式 (other API / mobile / SPA) |
| 認證 | session cookie | token (JWT / OAuth) |
| Format | HTML | JSON / XML / Protobuf |
| 攻擊面 | 表單 / button / link | endpoint / parameters / headers |
| Discovery | 看 page | 看 OpenAPI / 反編 mobile app |
| Common bug | XSS / CSRF | BOLA / Mass Assignment |

OWASP API Top 10 (2023) 反映**API-specific** 風險。

## OWASP API Top 10 2023

| # | 名稱 | 對應 web Top 10 |
|---|---|---|
| API1 | Broken Object Level Authorization (BOLA) | A01 IDOR |
| API2 | Broken Authentication | A07 |
| API3 | Broken Object Property Level Authorization | A01 + A04 |
| API4 | Unrestricted Resource Consumption | A04 |
| API5 | Broken Function Level Authorization | A01 |
| API6 | Unrestricted Access to Sensitive Business Flows | A04 |
| API7 | Server Side Request Forgery (SSRF) | A10 |
| API8 | Security Misconfiguration | A05 |
| API9 | Improper Inventory Management | A05 + A06 |
| API10 | Unsafe Consumption of APIs | A06 + A08 |

跟 web Top 10 重疊但角度不同。

## API1: Broken Object Level Authorization (BOLA)

「**API IDOR**」 — 單個 object 沒做 authz check。

```
GET /api/users/123/profile     ← user A's profile
attacker 改 /api/users/124     ← 看 user B
```

**API 中最常見漏洞**。OWASP 報告 BOLA 在被測 API 中**95%** 出現。

### 為什麼 API 比 web 更容易 BOLA

- API 多 endpoint，每個都要 check
- developer 假設「**前端會 filter**」（前端不會！）
- ID 通常 sequential / guessable
- 沒 UI，只有 endpoint，看不出 ownership

### 攻擊模式

```bash
# 註冊 user A
curl -X POST /api/users -d '{"email":"a@a.com","password":"pw"}'
# 註冊 user B
curl -X POST /api/users -d '{"email":"b@b.com","password":"pw"}'

# Login A，拿 token
TOKEN=$(curl -X POST /api/login -d '{"email":"a@a.com","password":"pw"}' | jq -r .token)

# 用 A 的 token 查 B 的 profile
curl -H "Authorization: Bearer $TOKEN" /api/users/2/profile
# 該回 403，但實際回 200 + B's data → BOLA
```

### 防禦

每個 endpoint 都做 object-level check：

```python
@app.route('/api/users/<int:user_id>/profile')
@require_auth
def get_profile(user_id):
    if user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return User.get(user_id).to_dict()
```

**framework middleware 不夠** — 必須**每個** object 操作 check。

進階：

- 用 **GUID** 不用 sequential ID（增加猜測難度，但**不是真防禦**）
- 用 **CASL / oso** 等 authorization library
- automated **authz testing**（每 endpoint 用 user A token 試 user B object）

## API2: Broken Authentication

跟 web A07 類似但 API 更嚴重，因為：

- API 自動化攻擊容易（curl loop）
- API 沒 UI 警告（user 不會「看到怪事」）
- token 泄漏容易（log / share / GitHub commit）

### 常見錯

#### 1. JWT 漏洞

回 Ch 12（alg=none / 弱 secret / algo confusion）。

#### 2. 無 rate limit

login endpoint 沒 rate limit → brute force：

```bash
for pw in $(cat passwords.txt); do
  curl -X POST /api/login -d "{\"email\":\"admin@a.com\",\"password\":\"$pw\"}" | grep token
done
```

10 萬密碼幾分鐘跑完。

#### 3. Reset token 弱

```
GET /api/reset?token=12345
```

attacker 試 1, 2, 3, ...

#### 4. API key 在 GitHub

```python
API_KEY = "sk_live_abc123..."   # commit 進 GitHub → 公開
```

GitHub 自動 scan 通報常見 vendor key，但 custom key 不通報。

### 防禦

- 強 password policy + bcrypt
- rate limit (per IP + per account + global)
- short JWT expiration + refresh token
- secret rotation
- 不在 source / log 放 secret
- MFA（API 也該支援）

## API3: Broken Object Property Level Authorization

「**object 中某些 property 不該所有 user 看 / 改**」。

### Excessive data exposure

API 回**整個 object**，包含 sensitive field：

```json
GET /api/users/123
{
  "id": 123,
  "name": "Alice",
  "email": "alice@a.com",
  "is_admin": false,
  "internal_notes": "VIP customer",
  "ssn": "123-45-6789",        ← 不該回給 client！
  "password_hash": "$2b$..."   ← 災難！
}
```

「**前端只用 name + email**」不代表 backend 只該回那兩個。

### Mass assignment（property 寫入）

回 Ch 9。client 改自己 profile：

```json
PUT /api/users/123
{
  "name": "Alice Smith",
  "is_admin": true       ← 該被 reject
}
```

爛 server：

```python
user.update(request.json)   # 全部 field 都吃 → 變 admin
```

### 防禦

#### 1. Schema-based 序列化

定義「**回給 client 的 schema**」，只 expose 必要 field：

```python
# Marshmallow
class UserPublicSchema(Schema):
    id = fields.Int()
    name = fields.Str()
    email = fields.Email()

# 不 expose ssn / password_hash / is_admin
```

#### 2. Whitelist update fields

```python
ALLOWED = {'name', 'email'}
data = {k: v for k, v in request.json.items() if k in ALLOWED}
user.update(data)
```

#### 3. Different schema per role

admin 跟 user 看的 user object 不一樣：

```python
if current_user.is_admin:
    return UserAdminSchema().dump(user)
else:
    return UserPublicSchema().dump(user)
```

## 動手練習

**1. 找 BOLA in Juice Shop**

challenges:

- "View Basket"
- "View Another User's Basket"
- "Forged Feedback"

**2. 寫 vulnerable API**

```python
from flask import Flask, request, jsonify

app = Flask(__name__)
users = {1: {"name": "Alice", "email": "a@a.com", "ssn": "123-45-6789"},
         2: {"name": "Bob", "email": "b@b.com", "ssn": "987-65-4321"}}

@app.route('/api/users/<int:id>')
def get_user(id):
    return jsonify(users.get(id))   # BOLA + Excessive data exposure
```

```bash
curl http://localhost:5000/api/users/1
# {"name":"Alice","email":"a@a.com","ssn":"123-45-6789"}  ← ssn 不該回
```

修：

```python
@app.route('/api/users/<int:id>')
@require_auth
def get_user(id):
    if id != current_user.id and not current_user.is_admin:
        abort(403)
    user = users.get(id)
    return jsonify({"name": user["name"], "email": user["email"]})   # 不回 ssn
```

**3. Mass assignment 測試**

對自己 PUT endpoint 試塞各種 field（is_admin, role, balance, etc）看 server 是否 reject。

**4. Burp authz testing**

裝 Authorize extension：

- 設「**user A 的 cookie**」
- browse 整個 app
- Authorize 自動用「**user B 的 cookie**」重發每 request
- 找差異 → BOLA candidate

**5. PortSwigger API testing labs**

https://portswigger.net/web-security/api-testing

新（2024）lab，免費。

## 自我檢核

- [ ] 知道 API Top 10 跟 web Top 10 差別
- [ ] BOLA 概念清楚
- [ ] 知道 object-level vs function-level authz 差別
- [ ] Excessive data exposure + Mass assignment 用 schema 防
- [ ] 至少對 1 個 endpoint 測過 BOLA
- [ ] Juice Shop API challenges 完成 2+

下一章看 API4-7。

→ [Ch 21 API4-7](./21-api-top10-part2.md)
