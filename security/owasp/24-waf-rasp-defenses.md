# Ch 24 — WAF / RASP 防禦產品深入

> 目標：搞懂 WAF 跟 RASP 的差別、主流產品（Cloudflare / ModSecurity / Imperva / Contrast）、各自適用場景。

## 防禦產品光譜

```
 攻擊路徑
  Internet
     │
     ▼
 ┌───────────┐
 │ DDoS / CDN │  Cloudflare / Akamai
 └─────┬─────┘
       ▼
 ┌───────────┐
 │  WAF      │  Web Application Firewall (網路層)
 └─────┬─────┘
       ▼
 ┌───────────┐
 │ Web App   │
 │  + RASP   │  Runtime Application Self Protection (內嵌 app)
 └─────┬─────┘
       ▼
 ┌───────────┐
 │   DB      │
 └───────────┘
```

每層擋不同層級攻擊。**多層 defense in depth**。

## WAF (Web Application Firewall)

「**HTTP-aware firewall**」 — 看 HTTP request 內容，按 rule 擋 / 改。

vs network firewall：

| 維度 | Network firewall | WAF |
|---|---|---|
| 層級 | L3-L4 (IP/port) | L7 (HTTP) |
| 看什麼 | IP / port / protocol | URL / parameter / header / body |
| 擋什麼 | port scan / DDoS | SQL injection / XSS / OWASP attacks |

### WAF 工作模式

#### 1. Negative model (黑名單)

「**這些 pattern 是攻擊，擋掉**」：

- SQL injection signature
- XSS pattern
- Path traversal
- Known CVE exploit pattern

最常見。但**容易 bypass**（unicode / encoding / 變形）。

#### 2. Positive model (白名單)

「**只允許這些 pattern**」：

- 嚴格 input format（正則）
- expected URL / parameter
- whitelist file extensions

更安全但**難維護**（每加 endpoint 都要更新）。

#### 3. ML / behavior-based

「**異常 traffic 偵測**」 — 跟 baseline 比對：

- request rate
- pattern frequency
- geographic anomaly

新趨勢，但 false positive 高。

## 主流 WAF 產品

### 1. Cloudflare WAF

Cloud-based WAF + DDoS protection + CDN 一體：

- DNS 改到 Cloudflare → 流量先過 Cloudflare → 再到你 server
- 預設 ruleset（OWASP Core Rule Set + Cloudflare 自家）
- 可 custom rule
- Free tier 基本 protection

優點：

- **設定極簡**（DNS 改一下）
- 全球 CDN（順便加速）
- DDoS 自動擋

缺點：

- 流量過 Cloudflare（隱私 / 信任）
- 付費版才有完整功能（Pro: $20/月、Business: $200/月、Enterprise）

### 2. AWS WAF

AWS 雲端 WAF，整合 CloudFront / API Gateway / ALB：

- Pay-per-request
- 可寫 rule（regex / size / IP / rate-based）
- AWS managed rule set
- 跟 AWS 服務深度整合

適合 already-on-AWS 的客戶。

### 3. ModSecurity

開源 WAF，Apache / nginx / IIS module：

- 用 OWASP Core Rule Set (CRS)
- 規則 file 可自訂
- 完全 self-host
- **學 WAF 內部最好的選擇**

```nginx
# nginx + ModSecurity
load_module modules/ngx_http_modsecurity_module.so;

http {
    modsecurity on;
    modsecurity_rules_file /etc/nginx/modsec/main.conf;
}
```

### 4. Imperva (商業)

頂級商業 WAF。on-prem / cloud。

特色：

- 很強的 ML detection
- ATO (Account Takeover) protection
- API security
- 貴（年費 $20K+）

企業 / Fortune 500 用。

### 5. F5 BIG-IP ASM (商業)

ADC (Application Delivery Controller) + WAF。enterprise 主流。

### 6. NAXSI (nginx)

輕量 nginx WAF module，純白名單模式。

## OWASP Core Rule Set (CRS)

開源 WAF rule set，**全 WAF 的基礎**：

- ModSecurity 預設用
- Cloudflare / AWS WAF 也參考
- 涵蓋 OWASP Top 10 + 常見 CVE

```
github.com/coreruleset/coreruleset
```

幾百個 rule，分類：

- 920000: Protocol attacks
- 930000: LFI
- 940000: XSS
- 941000-944000: SQLi
- 950000: Outbound (data leak)
- ...

## WAF 局限

### 1. Bypass 容易

```
攻擊                       常見 bypass
SQL injection              encoding (HTML / URL / Unicode)
XSS                        polyglot payload
LFI                        double encoding (`%252e%252e`)
```

WAF 用 regex / signature → encoding 變形繞過。

### 2. False positive

嚴 ruleset → block 正常 traffic：

- 含特殊字元的 user input
- 大 file upload
- API 含 unusual format

production 通常需要 tune（disable 某些 rule for 某些 endpoint）。

### 3. 不防業務邏輯漏洞

WAF 看 HTTP，不看業務邏輯。BOLA / 業務 abuse / race condition 它擋不了。

### 4. SSL 問題

WAF 必須能解 HTTPS → 需要 cert（cert 給 WAF）→ 擴大攻擊面。

### 5. 加 latency

每 request 過 WAF 加 1-50ms latency。

## RASP (Runtime Application Self-Protection)

「**內嵌在 application 裡**」的 protection。

vs WAF：

| 維度 | WAF | RASP |
|---|---|---|
| 位置 | Network layer | Application layer (inside JVM / runtime) |
| 看什麼 | HTTP request | function call / SQL query / file access |
| 知道什麼 | request/response | application logic + context |
| Latency | network round-trip | inline (microsecond) |
| Deploy | nginx config | install agent / library |

### RASP 怎麼運作

例：Java RASP agent

```
JVM 啟動 → load RASP agent → 攔特定 method (e.g. SQL execute)
↓
正常 SQL: db.execute("SELECT * FROM users WHERE id=?", [123])  ← OK
SQL injection: db.execute("SELECT * FROM users WHERE id='1' OR '1'='1'")  ← RASP detect → block
```

RASP 看「**這 SQL string 是不是 injection 後產生的**」 — 從上下文推斷，不是 pattern matching。

### 主流 RASP

- **Contrast Security** (商業, Java/.NET/Python/Node)
- **Imperva RASP**
- **Sqreen** (現屬 Datadog, focus app monitoring + RASP)
- **OpenRASP** (開源, baidu)

### RASP 優缺

優點：

- **零 false positive**（看 context，不是 pattern）
- 防 0-day（即使沒 signature，看行為偏離）
- 內部攻擊也擋（lateral movement）

缺點：

- **效能 overhead**（5-15%）
- 必須 install agent → 需要 ops 配合
- 商業產品貴

## WAF + RASP 組合

理想：

- WAF 擋 mass scan / 已知 attack（broad，便宜）
- RASP 擋 0-day / business logic（深度，貴）

99% production 只用 WAF。RASP 在金融 / 高敏資料才常見。

## 一個常見誤解：「WAF 就能保證安全」

**錯**。WAF 只是 defense in depth 一層。

- 弱配置 / bypass / business logic — WAF 救不了
- 「**WAF + 安全 code + monitoring + incident response**」才完整

## 一個常見誤解：「免費 WAF 跟付費差不多」

**部分對**。Cloudflare Free 對 small site 夠：

- DDoS 基本擋
- OWASP CRS
- bot challenge

但 advanced：

- ML detection
- ATO protection
- API security
- Custom rule

要 Pro / Business / Enterprise。

對 production 來說 $20-200/月 WAF 是良好投資。

## 一個常見誤解：「WAF 影響性能」

**部分對**。1-50ms latency。但對多數 web app 比 client-side render 時間小很多。

且 CDN-WAF 可能**反而加快**（CDN cache + 邊緣處理）。

## 動手練習

**1. 設 Cloudflare**

- 註冊 cloudflare.com
- 加 domain
- DNS 切到 Cloudflare nameserver
- 開「Proxy」（橘雲）
- WAF 設預設 ruleset

5 分鐘搞定。

**2. ModSecurity self-host**

```bash
# nginx + ModSecurity (Docker 最簡)
docker run -d -p 8080:80 owasp/modsecurity-crs:nginx

# 對 attack 試
curl 'http://localhost:8080/?id=1%27%20OR%201=1--'
# 應該被 block (403)
```

**3. WAF bypass 練習**

對自己的 ModSecurity，試 bypass：

```bash
# 直接攻 → block
curl "http://target/?id=' OR 1=1--"

# 大小寫繞
curl "http://target/?id=' Or 1=1--"

# Comment
curl "http://target/?id='/**/OR/**/1=1--"

# Encoding
curl "http://target/?id=%27%20OR%201=1--"
```

哪些 bypass，哪些被擋。

**4. CRS 規則閱讀**

```bash
git clone https://github.com/coreruleset/coreruleset
cd coreruleset/rules
ls *.conf
cat REQUEST-942-APPLICATION-ATTACK-SQLI.conf | head -100
```

讀真實 SQL injection detection rules。

**5. 看 WAF log**

跑你 site 一陣子，看 WAF 擋了什麼：

- Cloudflare Dashboard → Security → Events
- ModSecurity: `tail -f /var/log/modsec_audit.log`

實際 internet 的攻擊密度會驚訝你。

## 自我檢核

- [ ] WAF vs network firewall 差別
- [ ] WAF 3 種模式（黑 / 白 / ML）
- [ ] 5+ 主流 WAF 產品
- [ ] OWASP CRS 是什麼
- [ ] WAF bypass 為什麼可能
- [ ] RASP vs WAF 差別
- [ ] 至少設過 1 個 WAF（Cloudflare / ModSecurity）

下一章看安全 SDLC + threat modeling。

→ [Ch 25 安全 SDLC + threat modeling](./25-secure-sdlc.md)
