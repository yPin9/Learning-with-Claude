# Ch 4 — A01 Broken Access Control

> 目標：搞懂 OWASP 排第一的風險 — 越權 / IDOR / 垂直 / 水平越權，怎麼攻、怎麼防。

## 為什麼是 #1

OWASP 2021 統計：**94% 應用程式被測試發現至少 1 個 access control 漏洞**。是 OWASP 史上**從未掉出 Top 5** 的類別。

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

## 攻擊技巧

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

## 防禦原則

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

## 真實案例：USPS（2018）

USPS 網站的 Informed Visibility API：

- 有 6000 萬用戶資料
- 任何登入用戶能 query「**任何別人的**」郵件追蹤資料、地址、email
- IDOR + 沒做 object-level access check

被研究員發現後 USPS **拒絕修一年多**才修。

教訓：**大組織也常犯 IDOR，因為功能設計時沒想到 access boundary**。

## 動手練習

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

## 自我檢核

- [ ] 講得出垂直 vs 水平越權差別
- [ ] 知道 IDOR 為什麼最常見
- [ ] 6+ 種 IDOR 攻擊方法（改 ID / method / header / JWT / race）
- [ ] 防禦 5 原則記得
- [ ] 在 Juice Shop / DVWA 實作過 IDOR
- [ ] 寫 / 修過 vulnerable code

下一章看 A02 Cryptographic Failures — 加密做錯。

→ [Ch 5 A02 Cryptographic Failures](./05-a02-cryptographic-failures.md)
