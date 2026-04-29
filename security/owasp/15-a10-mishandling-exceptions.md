# Ch 15 — A10 Mishandling of Exceptional Conditions

> 目標：搞懂 2025 全新加入的這個類別 — error / exception / edge case 處理不當怎麼變成安全漏洞，以及怎麼防。

> **2025 全新類別**：A10 是 2025 版唯一全新加入的類別（A03 supply chain 是擴張原有類別、不算純新）。社群與 OWASP 統計團隊都看到大量真實事件**根本原因不是「沒驗證」也不是「加密弱」，而是「異常情境沒處理好」**，所以給它一個獨立位置。SSRF 不再單獨佔 A10（已併進 A01）。

## 「Mishandling of Exceptional Conditions」是什麼

定義很廣，核心是：**程式遇到「正常 happy path 以外的情況」時，做出有安全後果的錯誤行為**。

「正常以外」包括：

- 例外（exception / panic / signal）
- error code / 4xx / 5xx 回應
- timeout / network failure
- 邊界值（empty string、巨型輸入、負數、NaN、null）
- 並行 / race condition / TOCTOU
- partial failure（一個 transaction 中間斷掉）

對應的 CWE 包：CWE-754（Improper Check for Unusual Conditions）/ CWE-755（Improper Handling of Exceptional Conditions）/ CWE-703（Improper Check or Handling）/ CWE-209（Information Exposure Through Error）。

關鍵句：**happy path 正確不等於程式正確**。攻擊者最會操作 unhappy path。

## 6 種典型 mishandling

### 1. Fail-open（壞事預設放行）

驗證 / 授權 / 限速失敗時，預設「**讓他過**」：

```python
# 爛 code
try:
    if auth_service.is_admin(user):
        return admin_panel()
except Exception:
    return admin_panel()   # ← auth service 掛掉就放行，是不是瘋了
```

**正確：fail-closed（拒絕）**：

```python
try:
    if not auth_service.is_admin(user):
        abort(403)
    return admin_panel()
except Exception:
    log.error(...)
    abort(503)   # ← service degrade 也不能順便升權
```

真實案例：很多 WAF / SSO middleware 在 backend timeout 時 fail-open，攻擊者只要拖累 backend 就能繞過。

### 2. Race condition / TOCTOU

「**Time Of Check vs Time Of Use**」中間有 gap，state 變了。

```python
# 爛 code（轉帳）
def transfer(src, dst, amount):
    if balance(src) >= amount:        # check
        deduct(src, amount)            # use
        credit(dst, amount)
```

10 個 thread 同時打 → 都通過 check → 都成功 deduct → 帳戶被透支。

修：

```python
def transfer(src, dst, amount):
    with db.transaction(isolation='SERIALIZABLE'):
        # 或用 row lock: SELECT ... FOR UPDATE
        if balance_locked(src) >= amount:
            deduct(src, amount)
            credit(dst, amount)
        else:
            raise InsufficientFunds()
```

也常出現在：

- file upload / file rename（symlink race）
- token 一次性使用（同 token 多次平行 redeem）
- inventory 庫存（10 個人搶 1 件）
- session 升權（同一 session 多 thread 同時改 role）

### 3. Information disclosure via error

exception 把內部資訊回給 client：

```
500 Internal Server Error
Traceback (most recent call last):
  File "/app/api/users.py", line 42, in get_user
    user = db.query(...)
psycopg2.OperationalError: FATAL: password authentication failed
for user "myapp_admin", host "10.0.5.12"
```

attacker 拿到：

- 內網 IP
- DB user name
- ORM / framework 版本
- file path（情報 + path traversal 線索）

防禦：

- production `DEBUG=False`
- 統一 error handler：對外回 generic message + correlation ID，詳細 log 到 server side
- 不把 stack trace 放 response

```python
@app.errorhandler(Exception)
def handle(e):
    rid = log_with_id(e)
    return {"error": "Internal error", "request_id": rid}, 500
```

### 4. 沒處理 partial failure → 不一致 state

兩步操作中第一步成功、第二步失敗，沒 rollback / compensation：

```python
# 爛
charge_credit_card(amount)        # ✓ 扣款成功
send_order_to_warehouse(order)    # ✗ 網路 timeout → 沒下單
# user 被扣錢但沒收到貨
```

修法看場景：

- 同 service 內：DB transaction 包住
- 跨 service：saga pattern（補償交易）/ outbox pattern（先寫意圖、async 處理）/ idempotent retry
- 至少要有 manual reconciliation job 找出不一致

### 5. Resource leak on error path

happy path 釋放了資源，error path 沒：

```python
# 爛
def read_secret():
    f = open("/etc/secret")
    data = parse(f.read())   # 解析錯就 raise，f 沒 close
    f.close()
    return data
```

長期：file descriptor / connection / lock 漏 → DoS。

修：`with` / `try ... finally` / RAII：

```python
def read_secret():
    with open("/etc/secret") as f:
        return parse(f.read())
```

### 6. Exception 吞掉 / 處理錯

```python
# 爛
try:
    verify_signature(payload)
except Exception:
    pass    # ← 簽章驗失敗當沒事
process(payload)
```

或更隱晦：

```python
try:
    permission_check()
except SomeSpecificException:
    handle()
# 沒寫 except 的其他 exception 全部變未授權通過
```

原則：

- catch **specific exception**，不要 bare `except`
- 安全相關的失敗一律向上 propagate（讓上層 fail-closed）
- log 一定要包含這次失敗（後面 A09）

## 真實案例

### Knight Capital（2012）— 4.4 億美元 45 分鐘蒸發

升級交易系統時，8 台 server 中 1 台沒部署到新 code。新 code 用了一個舊 flag bit，舊 server 看到 flag 啟動了 8 年沒用的 testing routine。

**錯在哪**：

- 系統面對「不一致版本」沒 fail-closed（該停下檢查，而不是硬跑）
- error message 沒觸發告警（A09 也失敗）
- 沒有 kill switch

結果 45 分鐘下了 400 萬筆錯單，公司在那天破產（被收購）。

雖然不是傳統「web 漏洞」，但 OWASP 2025 把 A10 拉出來部分就是這類事件 — **異常處理錯誤造成商業 / 安全災難**比寫錯 SQL 還大。

### Cloudflare Regex（2019）

一條新 WAF regex 在某些 input 上 catastrophic backtracking → 100% CPU → 全球邊緣節點半癱 27 分鐘。

**錯在哪**：

- regex 本身沒 timeout / resource cap
- WAF rule deploy 沒 staged rollout
- exceptional input（特殊字串）的處理沒 bound

### GitLab DB 刪除事件（2017）

DB admin 在 production 跑 `rm -rf` 試圖修一個 lag 的 secondary，**但實際在 primary 上跑了**。發現後 5 個 backup 機制 4 個沒運作（exception path 從沒測過）。

教訓：**備份的還原 path 也要測**。「平常用不到」的 path 出事時最容易爛。

### log4j 2.16+ 的處理（2021）

Log4Shell（A03 講過）後，2.15 仍有殘餘問題、2.16 又有 DoS（`Thread.interrupt` 引發），到 2.17 才穩。每個 patch 是因為**前一個 patch 沒處理某個 exceptional input**。

連續 3 patch 才修完同一個類別問題 — 異常處理本身就難。

## 防禦原則

### 1. fail-closed by default

所有 security-relevant decision（auth / authz / rate limit / signature / WAF）—**例外狀況一律拒絕**，不是放行。

### 2. 把 input 邊界當 first-class

對每個 input：

- empty / null
- 超長
- 特殊字元（NUL byte、控制字元、Unicode normalization）
- 數值邊界（0、負、INT_MAX、NaN、Infinity）
- 重複 / 重送

寫 test 涵蓋這些。

### 3. 對 timeout 與 retry 設定明確策略

- 每個外部呼叫都設 timeout
- retry 必須 idempotent，不然 retry 比錯更慘
- exponential backoff + jitter
- circuit breaker（連續 fail 就先 open）

### 4. transaction / saga / idempotency

跨多步操作至少做到下面三選一：

- ACID transaction（同一 DB / service）
- saga pattern（業務級補償）
- 操作 idempotent + 對帳 job

### 5. 統一 error response

對外永遠 generic：`{"error": "Internal error", "request_id": "..."}`。詳細塞 server log。dev / production 兩套設定。

### 6. concurrency 永遠假設會發生

並行 bug 不是「會不會發生」，是「什麼時候被攻擊者發現」。對狀態變更操作：

- DB row lock（`SELECT ... FOR UPDATE`）
- 高 isolation level（serializable）
- 加 idempotency key
- 寫 test 用工具（Go race detector / Java JCStress / 自己 spawn N goroutines / threads 打）

### 7. error path 也要測

Chaos engineering / fuzz / fault injection：

- kill DB / Redis 看 app 反應
- 慢 network 看 timeout 處理
- malformed input

「**從沒跑過的 code path 一定有 bug**」。

## 動手練習

**1. 寫一個 fail-open bug + 修**

```python
# vuln.py
from flask import Flask, request, abort
import requests

app = Flask(__name__)

@app.route('/admin')
def admin():
    try:
        r = requests.get('http://auth:8080/check', params={'u': request.args.get('user')}, timeout=1)
        if r.json().get('admin'):
            return "Admin panel"
        abort(403)
    except Exception:
        return "Admin panel"   # ← BUG：auth service down 就放行
```

攻擊：把 `auth:8080` 路由改成 black hole（iptables DROP）→ 所有人變 admin。修成 fail-closed。

**2. 寫 race condition 練習**

```python
# 銀行帳戶。balance 起始 100。
# spawn 10 個 thread 同時 transfer 100 元出去。
# 觀察 balance 變多少。

import threading
balance = 100

def withdraw(amt):
    global balance
    if balance >= amt:
        # 故意製造 race
        time.sleep(0.001)
        balance -= amt
        return True
    return False

threads = [threading.Thread(target=withdraw, args=(100,)) for _ in range(10)]
for t in threads: t.start()
for t in threads: t.join()
print(balance)   # 經常 < 0
```

修：用 `threading.Lock()` / DB transaction。

**3. 找一個會 leak 內部資訊的 error**

```python
# vuln.py
@app.route('/user/<id>')
def user(id):
    return db.query(f"SELECT * FROM users WHERE id={id}")[0]
```

請求 `/user/abc` → 故意觸發 SQL error → response 含 stack trace + DB schema。修：error handler + DEBUG=False。

**4. 寫一個 idempotent endpoint**

POST `/charge` 帶 `Idempotency-Key` header。同一 key 重打 N 次只實際扣款 1 次。

提示：DB unique index on key + insert-or-fetch 流程。

**5. Chaos test**

對自己 web app：

```bash
# kill DB 看 app 怎麼回應
docker stop your-postgres

# 再 curl 你 app 的 /api/users
curl -i http://localhost:5000/api/users
```

期待：500 + 通用錯誤訊息（不是 stack trace），server log 有詳細 entry。

**6. fuzz 你自己的 endpoint**

用 ffuf / wfuzz 對自己 endpoint 噴 payload 看哪些觸發 500。每個 500 都是一個未處理 exception，是 A10 候選。

```bash
ffuf -u 'http://localhost:5000/api/users?id=FUZZ' -w wordlists/exception-triggers.txt -mc 500
```

## 自我檢核

- [ ] 講得出 fail-open vs fail-closed 差別 + 為什麼預設 fail-closed
- [ ] 至少 3 種 race condition 場景能舉例 + 修法
- [ ] 知道 production / dev 的 error response 該怎麼分
- [ ] 寫過 `with` / `try-finally` 處理 resource cleanup
- [ ] 知道 saga / outbox / idempotency 各別解什麼問題
- [ ] 知道 Knight Capital / Cloudflare regex 為何屬於 A10
- [ ] 自己 app 跑過 chaos / fault injection
- [ ] 寫過 fuzz 找 unhandled exception

OWASP Top 10 2025 全部走完。下一個 Part 進主流工具（Burp / ZAP / sqlmap）。

→ [Ch 16 Burp Suite 完整](./16-burp-suite.md)
