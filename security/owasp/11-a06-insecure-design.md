# Ch 11 — A06 Insecure Design

> 目標：搞懂「**設計層面**」的安全問題 — business logic flaws、threat modeling 缺失、架構失策。framework 救不了。

> **2025 變動**：2021 是 A04，**2025 編號 A06**（位置變動，本質不變）。

## A06 是什麼（2021 新增的類別）

OWASP 2021 首度引進這類，2025 沿用。其他類別大多是「**implementation 層面**」的漏洞（程式寫錯）。Insecure Design 是「**設計層面**」 — 即使程式寫對，**設計就有問題**。

例：

- 業務流程有漏洞（同樣 coupon 能用無限次）
- 沒有 rate limit（被 brute force / scrape）
- 認證流程設計有漏（password reset 流程不安全）
- 沒考慮 abuse 場景

「**寫對 code 解不了 design 漏洞**」 — 必須回去重設計。

## 經典 design flaws

### 1. 業務邏輯漏洞

#### Coupon 重複使用

```
正常流程: user enter coupon → server check → mark used → deduct
攻擊: user 同時送 100 個 request 用同一 coupon → race condition → 100 次都過
```

修：

- DB 層 atomic update + unique constraint
- 或 distributed lock

#### 負數金額

```
正常: user 送 10 USD 給朋友
攻擊: user 送 -10 USD 給敵人 → 從敵人帳戶扣 10 給自己
```

修：input validation `amount > 0`。

#### Step skip

```
正常流程: 加購物車 → 付款 → 確認 → 出貨
攻擊: 直接 POST /confirm-shipment → 跳過付款 → 出貨
```

修：server 端強制流程順序，每步驗證 prereq。

### 2. 沒有 rate limit

API 沒 limit → 攻擊者：

- brute force 密碼（不同 user 試常用密碼，避開 single-account lockout）
- scrape 全 user 資料
- DoS

修：

- per-IP / per-user / per-endpoint rate limit
- exponential backoff
- CAPTCHA after N failures

### 3. 不安全的 password reset 流程

#### 弱 token

```
reset URL: /reset?token=12345
```

攻擊者試 1, 2, 3... 找有效 token。

修：

- Token >= 128 bit entropy
- 短 expire (15-60 分鐘)
- 一次性使用
- 綁 user

#### 把 token 寄到 user-controlled email

```
正常: 你輸入 email → server send token
攻擊: 你輸入「我的 email」（即使 system 是 victim 的 account） → token 來我這 → 我重設 victim password
```

修：server 用「**已綁定的 email**」，不接受 user 改 email。

#### 把新密碼直接寄 email

「**忘記密碼？我們把您新密碼寄信給您！**」 → email 系統 admin / 中間 server 都能看新密碼。

修：寄 reset link，user 自己設新密碼。

### 4. 沒區分用戶 enumeration

```
登入頁：
- 帳號錯：「帳號不存在」
- 密碼錯：「密碼錯誤」
```

攻擊者用 email 列表測試「**這 email 註冊了嗎**」 → 隱私洩漏 + 後續 phishing 目標。

修：

```
- 帳號 / 密碼錯都回：「帳號或密碼錯誤」
- response time 一致（不要「不存在」就快回，「密碼錯」就慢回）
```

### 5. Mass assignment

```python
# 爛 code (Django)
@api_view(['POST'])
def update_user(request):
    user = request.user
    user.__dict__.update(request.data)
    user.save()
```

attacker：

```json
POST /update_user
{"name": "Alice", "is_admin": true}
```

`is_admin` 也被更新 → 升級為 admin。

修：

```python
# 白名單
ALLOWED_FIELDS = {'name', 'email'}
data = {k: v for k, v in request.data.items() if k in ALLOWED_FIELDS}
```

或用 framework 的 serializer (DRF / Marshmallow)。

### 6. Race condition / TOCTOU

「**Time-of-check vs time-of-use**」。

```python
# 爛
def withdraw(amount):
    if user.balance >= amount:    # check
        # ... 一些處理
        user.balance -= amount    # use
        save()
```

兩個 request 同時送 `withdraw(100)`：

```
Thread A: check balance=100, OK to withdraw 100
Thread B: check balance=100, OK to withdraw 100
Thread A: deduct → balance=0
Thread B: deduct → balance=-100  ← !
```

修：DB 層 atomic operation：

```python
# Postgres
UPDATE users SET balance = balance - %s WHERE id = %s AND balance >= %s
```

DB row lock 保證原子性。

## Threat Modeling

設計階段就思考「**怎麼被攻**」 — 預防 design flaw。

### STRIDE 框架

每個元件問 6 個威脅：

| Threat | 意義 | 例 |
|---|---|---|
| **S** poofing | 假冒身份 | session theft |
| **T** ampering | 改資料 | DB 被改 |
| **R** epudiation | 否認 | user 否認交易 |
| **I** nformation Disclosure | 洩密 | log leak |
| **D** enial of Service | 拒絕服務 | DDoS |
| **E** levation of Privilege | 提權 | user → admin |

### Data Flow Diagram

畫出資料怎麼流：

```
 User → [auth service] → Web app → [DB]
                              ↓
                          [3rd party API]
```

每個邊 + 每個 box，問 STRIDE。

### Abuser stories

跟 user stories 對立：

```
User story: "As a user, I can purchase items"
Abuser story: "As an attacker, I can purchase items with someone else's payment"
              "As an attacker, I can submit negative price"
              "As an attacker, I can spam orders to DoS"
```

每個 user story 配 3-5 個 abuser story。

## Defense in depth 設計

不依賴單一防禦：

```
 Layer 1: WAF
 Layer 2: Rate limit
 Layer 3: Auth check
 Layer 4: Input validation
 Layer 5: Prepared statement
 Layer 6: Output encoding
 Layer 7: Logging / monitoring
```

每層失效，下一層接住。攻擊者要破多層才成功。

## 真實案例：Starbucks gift card（2015）

Starbucks gift card 系統：

- user 能 transfer balance between cards
- 沒有 transactional 處理
- 攻擊者 race condition: transfer $5 多次 → 每次都從原卡扣 + 加新卡 → **gift card 餘額放大**

研究員報告，Starbucks 修了，但起初拒絕付 bug bounty（爭議幾個月）。

教訓：金錢相關 atomic 處理是底線；race condition 是金錢漏洞首選。

## 動手練習

**1. 找 Juice Shop 的 design flaw**

Juice Shop 有大量 business logic challenges：

- "Forged Coupon"
- "Negative Score Cheat"
- "Repeat Charges"
- "Login Admin"

每個都是 design flaw。

**2. 設計自己的 vulnerable app**

寫個簡單「轉帳」app，故意：

- 沒 rate limit
- balance check + deduct 不 atomic
- 沒區分 user enumeration

寫 attack script 重現每個漏洞。

**3. STRIDE on real app**

對你寫過 / 用過的 web app，跑 STRIDE：

- 列主要功能
- 對每個用 STRIDE 6 個維度問
- 寫下 5 個 threat + 對策

**4. PortSwigger Business Logic Vulnerabilities**

https://portswigger.net/web-security/logic-flaws

完整 lab。

**5. 寫 abuser stories**

對你 ongoing project，寫 10 個 abuser story。對 dev team 提出。

## 自我檢核

- [ ] 知道 A04 跟其他 OWASP 不同（design vs implementation）
- [ ] 5+ 種 design flaw 例子
- [ ] STRIDE 6 維度記得
- [ ] Race condition / TOCTOU 概念清楚
- [ ] 對自己 project 跑過 STRIDE
- [ ] Juice Shop business logic challenges 完成

下一章看 A07 Authentication Failures — 認證流程的常見漏洞（2025 從「Identification & Authentication Failures」縮短為「Authentication Failures」）。

→ [Ch 12 A07 Authentication Failures](./12-a07-auth-failures.md)
