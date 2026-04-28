# Ch 22 — API 8-10

> 目標：API8 Misconfiguration / API9 Inventory Management / API10 Unsafe Consumption of APIs。

## API8: Security Misconfiguration

跟 web A05 重疊，但 API 特有：

### 1. CORS 太寬

API 預設 `Access-Control-Allow-Origin: *` + cookie auth → CSRF / cross-origin theft。

### 2. 沒 HTTPS

API endpoint 接受 HTTP → token / data 明文。

### 3. Verbose error

```json
{
  "error": "DB error",
  "stack": "TypeError at /app/db.js:42\n  ...",
  "query": "SELECT * FROM users WHERE id=NaN"
}
```

leak schema / code 結構。

### 4. Default credentials

API 用 default API key 從不改。

### 5. Stale endpoints

舊 v1 API 還活著但沒人 maintain → 用過時 vulnerable code。

### 6. Permissive HTTP method

API 接受 OPTIONS / TRACE / DELETE 而沒 auth check。

### 7. Headers

缺 security header（HSTS / CSP / X-Content-Type-Options）。

### 防禦

- Hardening checklist
- Automated scan (nuclei misconfig templates)
- Configuration as code (Terraform / Ansible) 確保一致

## API9: Improper Inventory Management

「**不知道自己有哪些 API endpoint / 環境**」。

現實：

- 公司內部 API 數百個
- 多版本（v1, v2, v3 共存）
- staging / dev / prod 環境
- 第三方 API
- 文件過時 / 不存在

「**沒 inventory = 不知道暴露面 = 漏洞 lurk**」。

### 攻擊面

#### 1. 老 API version 還開

```
/api/v1/users   ← 5 年沒 patch，有 SQL injection
/api/v2/users   ← 修了
/api/v3/users   ← 最新
```

attacker 改 v3 → v1 → vulnerable。

#### 2. Staging / dev 環境暴露

```
api.example.com           ← prod, 嚴
api-staging.example.com   ← debug 開、auth 弱、含真資料 copy
api-dev.example.com       ← 更弱
```

attacker subfinder 找出來。

#### 3. 文件 vs 實際 endpoint 不符

OpenAPI 文件說有 endpoint A, B, C。實際還有 D, E, F（沒文件）→ unmonitored。

#### 4. 內部 API 對外暴露

「**這 API 只 internal 用**」 → admin 不知道路由器 NAT 把它對外 forward 了 → 公開。

### 防禦

#### 1. API inventory tooling

- **API gateway** 集中管理（Kong / Tyk / AWS API Gateway）
- 自動 discovery（traffic monitoring 找 API）
- API catalog tools（Postman API Network / Swagger Hub）

#### 2. 廢棄 API 真的下線

不只標 deprecated，要強制下線：

- 公告 sunset
- 設 hard deadline
- 過期 → 真的關掉

#### 3. 環境分離

dev / staging / prod 完全分開：

- 不同 DNS
- 不同 cred
- 不同 network
- staging 不該含 prod data（用 anonymized）

#### 4. OpenAPI as source of truth

- spec 寫了所有 endpoint
- CI 檢查實際 endpoint vs spec 一致
- 異常 endpoint 警告

#### 5. 從外面 audit

- 把自己 site 當外人，跑 subfinder / amass / nuclei
- 看找到什麼意外

## API10: Unsafe Consumption of APIs

「**你的 server 用第三方 API，但沒安全處理 response**」。

### 攻擊面

#### 1. 信任第三方 response

```python
def get_weather(city):
    r = requests.get(f"https://weather-api.com/?city={city}")
    return jsonify(r.json())   # 直接回 client
```

如果 weather API 被攻破 → 回 malicious payload → 你的 server 直接 forward → 你 client 中招。

#### 2. SSRF 第三方

```
你的 backend → 第三方 API → 第三方 backend
```

如果第三方有 SSRF → 攻擊者透過你 chain 到內部。

#### 3. 沒驗 response

第三方回 redirect → 你 follow → 連到 attacker server。

第三方回 size > 預期 → OOM。

第三方回過時 / wrong content type → 解析錯 → 攻擊者控制資料。

#### 4. Token 洩漏

第三方 API 要 API key → 你存 in plaintext / hardcode → 客戶（同產品其他 user）能取。

### 防禦

#### 1. 對第三方 response 用同 zero-trust

驗 signature / HMAC（如果第三方支援）  
驗 size limit  
驗 content type  
escape 對 client 顯示的內容

#### 2. Sandbox 第三方 call

- timeout
- size limit
- IP whitelist (如果第三方 IP 已知)
- separate network namespace

#### 3. Pin TLS cert

對 critical 第三方 (payment / auth)：cert pinning 防 MITM。

#### 4. Audit 第三方 security posture

合作前審：

- SOC 2 Type II
- ISO 27001
- security questionnaire

定期 review。

## 真實案例：MOVEit (CVE-2023-34362, 2023)

MOVEit Transfer (Progress Software) 有 SQL injection：

- Cl0p ransomware group exploit
- 影響 2700+ 公司，6500 萬人
- 多家銀行 / 政府機構

很多公司用 MOVEit 是「**第三方 file transfer service**」 → 一個 vendor 漏洞 → 全 client 中招。

教訓：API10 真實。第三方安全 = 你安全。

## 動手練習

**1. 對自己 site 做 inventory**

```bash
# 用 subfinder / amass 找 subdomain
subfinder -d yourdomain.com

# 對找到的 host 用 nuclei
nuclei -l hosts.txt -t ~/nuclei-templates/exposures/

# 找暴露的 dev / staging
ffuf -u "https://FUZZ.yourdomain.com" -w subdomains.txt
```

意外的 staging / admin / monitoring host 嗎？

**2. 對自己 OpenAPI spec 跟實際對比**

```bash
# 列 spec 的 endpoint
cat openapi.json | jq '.paths | keys'

# 對 site 跑 spider 找實際 endpoint
# 比較
```

不一致的（spec 沒、實際有）→ 隱藏 endpoint，audit 必要。

**3. 寫 vulnerable consumption**

```python
@app.route('/proxy')
def proxy():
    url = request.args.get('url')
    r = requests.get(url, timeout=300)   # 沒 timeout / size limit
    return r.content                       # 直接 forward
```

攻：URL 是個 attacker server, 回 100 GB → server OOM。

修：

```python
r = requests.get(url, timeout=5, stream=True)
content = r.raw.read(1024 * 1024)   # max 1 MB
```

**4. 第三方 API security posture**

對你公司用的 5 個第三方 API（Stripe / SendGrid / Twilio / Google / 其他）：

- 有 security page 嗎？
- SOC 2 / ISO 27001？
- 最近有 breach 嗎？
- key 怎麼 rotate？

寫成 risk register。

**5. PortSwigger API enumeration**

https://portswigger.net/web-security/api-testing/enumerating

API discovery / endpoint hunting labs。

## 自我檢核

- [ ] API8 misconfig 至少 5 種講得出
- [ ] API9 inventory 為什麼困難 + 防禦
- [ ] API10 unsafe consumption 攻擊面
- [ ] 對自己 site 跑過 subdomain enumeration
- [ ] 知道第三方 API security 是自己責任
- [ ] OpenAPI as source of truth 概念

Part 4 結束。練習 B 對 API 做完整 pentest。

→ [練習 B：API pentesting](./practice-b-api-pentest.md)
