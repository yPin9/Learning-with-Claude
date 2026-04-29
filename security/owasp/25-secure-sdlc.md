# Ch 25 — 安全 SDLC + threat modeling

> 目標：把安全融入軟體開發生命週期 (SDLC)，學 threat modeling、SAST/DAST/IAST。

## 為什麼需要 SDLC 安全

「**修 bug 越早越便宜**」 — 業界共識：

| 階段發現 | 修復成本 |
|---|---|
| Design | 1x |
| Coding | 5x |
| Testing | 10x |
| Production | 100x |

production 才發現 = 改 + 重 deploy + 處理後果（bad PR / 賠償）。

「**Shift Left**」 — 把安全往左推（早階段）。

## Secure SDLC 階段

```
 Plan → Design → Code → Test → Deploy → Operate
   ↓       ↓       ↓      ↓       ↓        ↓
 需求    威脅    安全   安全    Secure  監控/
 含安全  建模    coding test   deploy  IR
```

每階段有對應安全活動。

### 1. Plan / Requirement

- **安全需求**：什麼資料敏感？compliance？(GDPR / PCI-DSS / HIPAA)
- **abuser stories**（Ch 9 講過）

### 2. Design

- **Threat modeling**（核心）
- 架構審查
- 第三方 component 選擇 (security posture)

### 3. Code

- **Secure coding standards**（OWASP）
- code review
- SAST (Static Analysis)
- pre-commit hooks (secret scanning)

### 4. Test

- DAST (Dynamic Analysis)
- SCA (Software Composition Analysis — dependency scan)
- pentest
- IAST (Interactive)
- security regression test

### 5. Deploy

- Secure config (TLS / headers / 密鑰)
- Secret management
- Container scanning
- Pipeline security (Ch 13)

### 6. Operate

- WAF / RASP
- Logging / alerting (A09，2025 把 monitoring 改成 alerting)
- Incident response
- Bug bounty / responsible disclosure

## Threat Modeling

「**設計階段就思考怎麼被攻**」。

### STRIDE 框架（Microsoft）

每元件問 6 種威脅：

| Threat | 中文 | 例 |
|---|---|---|
| **S** poofing | 假冒身份 | session theft |
| **T** ampering | 改資料 | DB injection |
| **R** epudiation | 否認 | user 否認交易 |
| **I** nformation Disclosure | 洩密 | log leak |
| **D** enial of Service | 拒絕服務 | DDoS |
| **E** levation of Privilege | 提權 | user → admin |

### Data Flow Diagram (DFD)

畫資料流：

```
   User
    │
    ▼
 [Web Frontend] ───► [API Gateway] ───► [App Server] ───► [DB]
                          │                │
                          │                └─► [Cache]
                          │
                          └─► [Auth Service]
```

每個 box / arrow 對 STRIDE。

例：API Gateway → App Server arrow：

- **S**: 來自 API Gateway 的 request 真嗎？(mTLS)
- **T**: request 被 modify 嗎？(HMAC / signature)
- **R**: app server 知道是哪個 gateway 嗎？(audit log)
- **I**: data 加密嗎？(TLS internal)
- **D**: rate limit?
- **E**: gateway compromise → app server 信任？(zero trust)

### Trust boundary

DFD 上畫 trust boundary：

```
   ┌─────────────────────────┐
   │   Untrusted (Internet)  │
   └──────────┬──────────────┘
              │ Trust boundary
              ▼
   ┌─────────────────────────┐
   │   DMZ (Web tier)        │
   └──────────┬──────────────┘
              │ Trust boundary
              ▼
   ┌─────────────────────────┐
   │   Internal (App + DB)   │
   └─────────────────────────┘
```

跨 boundary 的 data 需要 validate。

### DREAD（評估 risk）

對每個威脅：

- **D** amage potential
- **R** eproducibility
- **E** xploitability
- **A** ffected users
- **D** iscoverability

每項 1-3 分，加起來排序。

業界已少用 DREAD（主觀），改用 CVSS scoring。

## SAST (Static Application Security Testing)

「**讀 source code 找漏洞**」。

工具：

- **Semgrep** (開源, 多語言)
- **SonarQube** (commercial / community)
- **Snyk Code**
- **Checkmarx**
- **Veracode**
- **CodeQL** (GitHub)

```bash
# semgrep
pip install semgrep
semgrep --config=auto /path/to/code

# 找特定 pattern
semgrep --config=p/python /path/to/code
```

### SAST 找什麼

- SQL injection (string concat)
- XSS sink (innerHTML / eval)
- Hardcoded secret
- Insecure crypto
- Path traversal
- Common CVE pattern

### SAST 限制

- False positive 多
- 不知 runtime context
- business logic 看不懂
- 找的多是「pattern matches」，不一定真漏洞

## DAST (Dynamic Application Security Testing)

「**對 running app 跑攻擊測試**」。

工具：

- **OWASP ZAP**（Ch 17）
- **Burp Suite Scanner**（Pro）
- **Acunetix**
- **AppSpider**

```bash
# ZAP baseline scan
docker run owasp/zap2docker-stable zap-baseline.py -t https://target
```

### DAST vs SAST

| 維度 | SAST | DAST |
|---|---|---|
| 看什麼 | source code | running app |
| 何時 | dev / CI | testing / staging |
| Coverage | 全 code | 跑得到的 endpoint |
| False positive | 多 | 少 |
| 找什麼 | pattern bug | actual exploit |
| 配置 | 有 source 即可 | 需要 running app |

兩者**互補**。

## IAST (Interactive Application Security Testing)

agent 內嵌 app，**runtime 看到 code execution + 看到 HTTP traffic**：

- 比 SAST 準（看實際執行）
- 比 DAST 深（看 internal state）
- 但需要 instrumentation

工具：

- **Contrast Security**
- **Synopsys Seeker**

production 級 RASP 通常含 IAST。

## SCA (Software Composition Analysis)

「**dependency 掃 known CVE**」。Ch 11 講過：

- npm audit / pip-audit / cargo audit
- Snyk
- Dependabot
- WhiteSource
- Sonatype

## Secret Scanning

掃 source / commit 找洩漏 secret：

- GitHub built-in
- TruffleHog
- gitleaks
- detect-secrets

```bash
trufflehog git https://github.com/your/repo
gitleaks detect --source .
```

CI integration：每 PR 跑 → block 含 secret 的 commit。

## Pipeline 全套整合

CI/CD 跑安全 stage：

```yaml
# .github/workflows/security.yml
name: Security

on: [push, pull_request]

jobs:
  sast:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: pip install semgrep
      - run: semgrep --config=auto --error

  dependencies:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: npm audit --audit-level=high
  
  secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: trufflesecurity/trufflehog@main

  dast:
    runs-on: ubuntu-latest
    needs: build
    steps:
      - run: docker compose up -d
      - run: zap-baseline.py -t http://localhost:3000
```

每 push 自動跑 SAST + dep scan + secret scan + DAST。

## OWASP Cheat Sheet Series

OWASP 官方提供針對每個主題的「**速查單**」：

```
https://cheatsheetseries.owasp.org/
```

100+ cheat sheets：

- Authentication
- Authorization
- SQL Injection Prevention
- XSS Prevention
- CSRF
- ...

寫 secure code 時對照看。**最 actionable 的安全資源**。

## 一個常見誤解：「裝 SAST 就安全」

**錯**。SAST 找的是 **pattern 漏洞**，多 false positive，且 business logic 看不懂。

「**SAST + DAST + manual code review + pentest + monitoring**」才完整。

## 一個常見誤解：「Threat modeling 太麻煩」

**部分對**。完整 STRIDE on 整個 system 累。但：

- 對 critical feature 做就好（不用全 system）
- 1 小時的 threat modeling 省幾天 incident response
- design 階段做最便宜

## 一個常見誤解：「security 是 security team 的事」

**錯**。Modern「**DevSecOps**」 — dev 也要會 secure coding，security team 提供 tooling + standard。

「**security as a shared responsibility**」。

## 動手練習

**1. 對 1 個 feature 做 STRIDE**

挑你寫過的 1 個 web feature（如「user 上傳 avatar」），跑完整 STRIDE：

```
Spoofing: 怎確認上傳者是 user 自己？
Tampering: image 內容能被改嗎？
Repudiation: log 上傳者？
Information Disclosure: 別人能看到別人 avatar 嗎？
DoS: 大 file / 大量 upload?
Elevation: 用 image 觸發 RCE?（image parser bug）
```

寫成 1 頁 doc。

**2. 跑 semgrep**

```bash
pip install semgrep
cd your-project
semgrep --config=auto

# 看 finding
```

**3. 對自己 GitHub repo 啟用 GitHub security**

- Settings → Security
- Enable Dependabot, Code scanning, Secret scanning

跑幾天看找到什麼。

**4. 對 1 個 feature 跑 manual security review**

挑個小 feature，自己 walk through code：

- 每 user input 怎麼 validate?
- 每 DB query 怎麼參數化?
- 每 output 怎麼 escape?
- 哪裡 access control check?
- error 怎麼處理?

**5. 寫 secure coding checklist**

寫一份你 team 用的 checklist (10-20 條)，作為 PR review 標準。

## 自我檢核

- [ ] SDLC 6 階段 + 每階段安全活動
- [ ] STRIDE 6 維度
- [ ] DFD + trust boundary 概念
- [ ] SAST / DAST / IAST / SCA 區別
- [ ] CI/CD security pipeline 寫過
- [ ] 知道 OWASP Cheat Sheet Series

下一章看 Bug bounty + responsible disclosure。

→ [Ch 26 Bug bounty 心法 + responsible disclosure](./26-bug-bounty.md)
