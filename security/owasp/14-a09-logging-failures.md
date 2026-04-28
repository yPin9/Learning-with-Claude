# Ch 14 — A09 Security Logging & Monitoring Failures

> 目標：理解為什麼「log + monitor」是安全的最後防線，以及怎麼做對。

## 為什麼這項排前 10

不能完全防止被攻 → 必須能**快速偵測 + 反應**。

統計：

- 平均 breach detection time：**200+ 天**
- 大部分被通報而非自己發現（client 抱怨 / FBI 通知 / blackmail）
- detect 早 = 損失少

「**沒 log = 不知道被攻 = 攻擊永遠成功**」。

## A09 包含什麼

- 該 log 的事件沒 log
- log 沒 monitor / alert
- log 沒保留夠久
- log 集中（不只在 server 上）
- log 自身被攻擊者修改 / 刪除

## 該 log 什麼

### 必 log

- **登入 / 登出**（成功 + 失敗）
- **權限變更**（user → admin）
- **敏感操作**（轉帳 / 刪除帳號 / export 資料）
- **錯誤** / 異常（含 stack trace）
- **API access**（含 IP / user agent）
- **管理操作**（admin actions）

### 不該 log

- **密碼 / API key / token**（明文進 log = 災難）
- **完整 credit card / SSN**（mask: `****-1234`）
- **private user data**（看法律 — GDPR 規定 minimum）

### 結構化 log

JSON 比純文字好：

```json
{
  "timestamp": "2025-04-28T12:34:56Z",
  "level": "WARN",
  "event": "login_failed",
  "user_id": null,
  "username": "admin",
  "ip": "1.2.3.4",
  "user_agent": "...",
  "reason": "invalid_password",
  "attempt_count": 5
}
```

```python
# Python 結構化 log
import structlog
log = structlog.get_logger()
log.warn("login_failed", username="admin", ip=ip, reason="invalid_password")
```

## 集中 log（centralized logging）

server 1 + server 2 + ... → 集中到一個 place（不在 server 本身）。

理由：

- **server 被攻 → log 被刪 / 改**
- 跨 server 關聯
- 統一搜尋

工具：

- **ELK stack**（Elasticsearch + Logstash + Kibana）
- **Loki + Grafana**（輕量替代）
- **Splunk**（商業）
- **Datadog / Sumo Logic**（SaaS）
- **CloudWatch**（AWS）

architecture：

```
server 1 ─┐
server 2 ─┼─► log shipper (filebeat) ─► central (ELK)
server 3 ─┘                                │
                                           ▼
                                       搜尋 / dashboard / alert
```

## 監控 + Alert

光 log 不夠 → 要 **alert**：

- 失敗 login > 100 次 / 分鐘 → alert（brute force）
- HTTP 500 突增 → alert（服務異常）
- 新 admin account 建立 → alert（可能被攻擊）
- 大量資料 download → alert（資料外洩）
- IP 從非預期國家登入 admin → alert

工具：

- **Grafana / Kibana** dashboard
- **Prometheus alertmanager**
- **PagerDuty / Opsgenie**（24/7 oncall）
- **Sentry**（錯誤追蹤）

## SIEM（Security Information and Event Management）

「**安全 log 集中分析**」：

- ELK 等 generic
- **Splunk Enterprise Security**
- **IBM QRadar**
- **Wazuh**（開源）
- **Microsoft Sentinel**

SIEM 提供：

- 關聯多 source（network / endpoint / app log）
- detection rule（如：5 次 failed login + 1 次 success → 暴力破解成功）
- threat intel feed（known bad IP / domain）
- alert + workflow

## SOC（Security Operations Center）

24/7 安全 team，配 SIEM，看 alert / 處理 incident。

大企業有自家 SOC，中小企業 outsource（MSSP — Managed Security Service Provider）。

## 真實案例：Target（2013）

零售商 Target 被偷 4000 萬 credit card：

- attacker 進供應商 → 跳到 Target 內部
- 安裝 POS malware → 偷 card
- **Target SIEM 偵測到了**！alert 出來了
- **但 SOC 沒 follow up**（被當作 false positive）
- 12 天後才被告知（外部）

「**有 log 但沒人看 = 等於沒 log**」。

教訓：

- alert 要 actionable（避免 alert fatigue）
- runbook：每個 alert 對應的 procedure
- regular alert review

## Log injection 攻擊

attacker 把 log 控制字元塞 input：

```python
log.info(f"User {username} logged in")
```

attacker username = `\nWARN: admin logged in successfully`

→ log 出現假 admin login 紀錄。

或更壞：log viewer 是 web UI，username 含 XSS payload → log viewer XSS。

修：

- 結構化 log（attacker 不能改 JSON 結構）
- log 顯示 escape

## Log retention

法規 + 安全考量：

- GDPR：personal data minimum
- PCI-DSS：1 年 online + 3 個月 archive
- HIPAA：6 年
- 一般 best practice：1 年 hot + 7 年 cold

但**安全 log 至少 6 個月**，因為 breach detection 平均 200 天。

## 不該 log 的踩雷

### 1. PII 在 log

```python
log.info(f"User registered: name={name}, email={email}, phone={phone}, ssn={ssn}")
```

GDPR 違規 + log leak 災難。

### 2. Secret 在 log

```python
log.debug(f"Auth header: {request.headers.get('Authorization')}")
log.debug(f"DB query: SELECT * FROM ... WHERE password='{password}'")
```

token / password 全在 log 裡。

### 3. Stack trace 到 client

production 不該對 client 顯示 stack trace（給 attacker fingerprint）。

server log 可以詳細。

## 防禦：心法

1. **log 該 log 的事件**（不只 INFO）
2. **不 log 該保密的**（密碼 / token / PII）
3. **集中 log**（不只 server local）
4. **alert + monitor**
5. **regular review**（避免 alert fatigue）
6. **playbook + runbook**（事件處理流程）
7. **incident response 演習**（事先練）

## 動手練習

**1. 自己 app 加結構化 log**

```python
import structlog
log = structlog.get_logger()

@app.route('/login', methods=['POST'])
def login():
    user = request.form['username']
    if check_password(...):
        log.info("login_success", user=user, ip=request.remote_addr)
        ...
    else:
        log.warn("login_failed", user=user, ip=request.remote_addr, reason="bad_password")
        ...
```

**2. 設 log shipping**

裝 ELK 或 Loki，把自己 app 的 log ship 過去。寫 dashboard / alert。

**3. 寫 detection rule**

對自己 log，定義 alert 條件：

- 同 IP 5 分鐘內 10+ failed login → alert
- HTTP 500 持續 1 分鐘 → alert
- admin 帳號被建 → alert

**4. 攻擊 + log 觀察**

對自己 vulnerable app 跑 sqlmap / hydra → 看 log 顯示什麼 / 你能 detect 嗎。

**5. ATT&CK simulation**

用 Atomic Red Team / Caldera 模擬攻擊：

```bash
git clone https://github.com/redcanaryco/atomic-red-team.git
# 跑某個 ATT&CK technique 的 simulation
# 看你 log 能不能抓到
```

## 自我檢核

- [ ] 知道該 log 哪些事件 / 不該 log 什麼
- [ ] 結構化 log vs 純文字 log 差別
- [ ] 集中 log 的價值（log 不能在 server local）
- [ ] 知道 ELK / Loki / Splunk / SIEM 等工具定位
- [ ] alert fatigue 是真問題
- [ ] 自己 app 至少有基本 log

下一章看 A10 SSRF — 最後一個 OWASP Top 10。

→ [Ch 15 A10 SSRF](./15-a10-ssrf.md)
