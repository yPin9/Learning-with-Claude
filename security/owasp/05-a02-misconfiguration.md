# Ch 5 — A02 Security Misconfiguration

> 目標：搞懂常見的「**設定錯**」漏洞 — default credentials、debug mode、verbose error、open S3 bucket、CORS misconfig。

> **2025 變動**：Misconfiguration 從 2021 的 #5 升到 **2025 的 #2**。社群票選與真實事件數據都顯示這類問題愈滾愈大（cloud / IaC / Kubernetes 把 misconfig 攻擊面爆量擴大）。

## 為什麼這項排第 2

Misconfiguration 是「**沒做好基本設定**」 — 不是 code bug，但可能更糟。攻擊面：

- 預設密碼 / 弱密碼
- debug page 開在 production
- error 訊息洩 stack trace
- cloud storage（S3 / GCS）公開
- 不必要的功能 / port 開
- 過時 framework default
- CORS 太寬

**多數 breach 不是 0-day，而是 misconfig**。

## 1. Default credentials

很多軟體預設帳密：

| 軟體 | Default |
|---|---|
| MongoDB（老版） | 無認證 |
| Redis | 無認證 |
| Elasticsearch（老版） | 無認證 |
| Jenkins | 預設無 admin |
| Tomcat manager | tomcat/tomcat |
| PostgreSQL `postgres` user | 不設密碼 |
| Cisco / 路由器 | admin/admin |
| WordPress wp-admin | 沒強制改 |

攻擊：

```bash
# Shodan 找 internet 上的 MongoDB
shodan search "product:MongoDB"
# 找到的 server 多數沒密碼，直接連

mongosh mongodb://1.2.3.4:27017
> show dbs
> use users
> db.find()
```

修：

- **必設密碼**，且強密碼
- 限制 bind address（127.0.0.1 / 內網）
- firewall block 外部
- 改預設 port（不是安全主防，但減 scan 噪音）

## 2. Debug mode 在 production

framework 的 debug page 洩太多：

### Flask debug page

```python
# vulnerable
app.run(debug=True)
```

任何 unhandled exception → debug page 含：

- stack trace
- source code
- environment variables（**含 secret**！）
- **執行任意 Python**（debug console）

production 開 debug = remote code execution。

### Django

```python
# settings.py
DEBUG = True   # production 必須 False
```

DEBUG=True 時，error page 含 stack / settings / SQL。

### 修

- 環境變數區分 dev / prod：

```python
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
```

- production deploy script 強制 check：

```bash
if [ "$DEBUG" = "True" ]; then
    echo "ERROR: DEBUG=True in production"
    exit 1
fi
```

## 3. Verbose error / stack trace

```
500 Internal Server Error

Traceback (most recent call last):
  File "/app/auth.py", line 42, in login
    cursor.execute(f"SELECT * FROM users WHERE name='{name}'")
  File "/usr/lib/python3.10/...mysql.py", line 200, in execute
    raise ProgrammingError(...)
ProgrammingError: You have an error in your SQL syntax...
```

錯誤訊息洩漏：

- 程式語言 / version
- 框架 / version
- file path
- code snippet
- DB 類型 / version
- SQL 內容

攻擊者用這些 fingerprint 找 known CVE。

修：

- production: 統一 generic error page
- log 詳細到 server log（不對 client 顯示）

```python
@app.errorhandler(500)
def server_error(e):
    log.exception(e)
    return "Internal Server Error", 500
```

## 4. 開放 S3 bucket / cloud storage

AWS S3 bucket 預設 private，但常常被誤設 public。

歷史 breach（巨量）：

- **Capital One 2019**：1.06 億 user data，misconfigured WAF + IAM 讓 attacker 進 S3
- **Verizon 2017**：1400 萬 customer 資料 in public S3
- **Accenture 2017**：4 個 public S3 bucket 含 master keys

掃 public S3：

```bash
# Various S3 enumeration tools
aws s3 ls s3://target-bucket --no-sign-request   # 不需要 auth
```

修：

- 預設 private（block public access）
- IAM policy 嚴格 least privilege
- enable S3 access logging
- AWS Config rule 自動 alert public bucket
- KMS encryption at rest

## 5. CORS 太寬（再講一次）

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Credentials: true
```

`*` + credentials 不能同時用（browser 會擋）。但若 server 動態 echo origin：

```python
# vulnerable
@app.route('/api/data')
def data():
    response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin')
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    ...
```

evil.com 的 JS 能用 user 的 cookie 讀任意 endpoint → 帳戶 takeover。

修：

- 白名單 origin
- 不對 sensitive endpoint 開 CORS
- credentials 盡量別用

## 6. 開太多功能 / port

server 開不必要的 service：

- FTP / Telnet（明文）
- old SSH algorithms
- DEBUG port
- admin endpoint 對外開

修：

- 列當前 listen port: `ss -tnlp`
- 關不必要的
- firewall block

## 7. 框架 default 不安全

某些 framework 預設不安全，要明確啟用 hardening：

| Framework | Default 不安全 |
|---|---|
| Flask | debug 預設 false 但 dev 模式開 |
| Express | 沒 security middleware（要裝 helmet） |
| Spring Boot Actuator | 端點預設暴露 |
| Tomcat manager | default cred |
| Laravel | `.env` 在 repo（如果 admin 沒設 .gitignore） |

修：

- 看 framework security docs
- 用 official hardening guide

## 8. HTTP method 沒限

```python
@app.route('/api/users/<id>')   # 預設 only GET
```

但 POST / PUT / DELETE 應該明確規定：

```python
@app.route('/api/users/<id>', methods=['GET'])
```

如果預設 allow 所有 method → CSRF + IDOR 風險。

## 9. Directory listing

nginx / Apache 預設可能開 directory listing：

```
http://example.com/uploads/
```

→ 列出整個 directory 內容 → backup file / source code 洩。

修 nginx：

```nginx
location / {
    autoindex off;   # 預設 off，但 confirm
}
```

## 10. Backup file 在 web root

`config.php.bak` / `app.py.swp` / `.git/` directory：

```bash
curl https://target.com/.git/HEAD
# 如果不 404 → .git 整個 leak → 拿到 source code

curl https://target.com/wp-config.php.bak
# 拿到 DB password
```

修：

- `.gitignore` 嚴格
- web root 不放 source / backup
- `nginx` block 敏感 path：

```nginx
location ~ /\.git { deny all; }
location ~ \.bak$ { deny all; }
```

## 真實案例：Equifax（再講）

Equifax breach 2017：

- 用過時 Apache Struts（CVE-2017-5638）
- patch 公開兩個月後沒上
- attacker 從 web app exploit RCE
- 內部 lateral：DB 沒分段 → 直接 read 敏感資料
- 監控失敗：3 個月才被發現

**多層 misconfig 累積成災難**。

## 自動化掃描

工具能掃 misconfiguration：

```bash
# nuclei templates
nuclei -t ~/nuclei-templates/misconfiguration -u https://target

# nikto
nikto -h https://target

# OWASP ZAP baseline
zap-baseline.py -t https://target
```

## 動手練習

**1. 找暴露的 .git**

```bash
# Public site 試（合法 only — bug bounty target / CTF）
curl -I https://target.com/.git/config

# Tools
git-dumper https://target.com/.git/ /tmp/dumped/
```

**2. 設 vulnerable Flask**

```python
from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    return "Hello"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')   # debug + 對外
```

訪問 `http://localhost:5000/raise_error` → 看 debug page。

修：debug=False。

**3. nuclei 自己 site**

```bash
nuclei -t ~/nuclei-templates/misconfiguration -u http://localhost:5000
```

**4. Shodan 尋常見 misconfig**

```
shodan.io 搜:
  product:MongoDB           ← 開放 Mongo
  http.title:"phpMyAdmin"   ← 暴露 phpMyAdmin
  port:9200 elasticsearch   ← 開放 ES
```

**只看，不要連**（可能違法）。

**5. AWS 自己 S3 audit**

```bash
aws s3api get-bucket-acl --bucket your-bucket
aws s3api get-bucket-policy --bucket your-bucket
```

確認沒 public access。

## 自我檢核

- [ ] 10 種常見 misconfiguration 講得出
- [ ] 知道 Flask debug=True 在 prod = RCE
- [ ] 知道 S3 bucket 預設 private 但常被誤設 public
- [ ] CORS misconfig 危險形式
- [ ] 用 nuclei / nikto 掃過至少 1 次
- [ ] 知道 `.git/` exposure 的危險

下一章看 A03 Software Supply Chain Failures — 你的 dependency / build pipeline / CI 都是攻擊面（2025 從 A06 升 A03 並大幅擴張）。

→ [Ch 6 A03 Software Supply Chain Failures](./06-a03-software-supply-chain.md)
