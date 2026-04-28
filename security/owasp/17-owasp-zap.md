# Ch 17 — OWASP ZAP

> 目標：認識 OWASP ZAP（免費 Burp 替代）的核心功能 + CI/CD 整合。

## ZAP 是什麼

**OWASP Zed Attack Proxy** — 開源、免費、跨平台 web pentest 工具。功能對標 Burp Pro。

特點：

- **完全免費**（vs Burp Pro $450/年）
- 內建 **Active Scanner**（Burp Community 沒有）
- API + CLI（容易自動化 / CI/CD 整合）
- BApp 等價：**Marketplace Add-ons**
- 但 GUI 比 Burp 醜

兩者**互補不取代**：

- 互動 / 手動 pentest：**Burp 順手**
- 自動化 / CI/CD：**ZAP 強**

## 安裝

```bash
# Linux
sudo apt install zaproxy

# Mac
brew install --cask zap

# Windows: download installer
# https://www.zaproxy.org/download/

# Docker (CI/CD 用)
docker pull owasp/zap2docker-stable
```

## 快速開始

```bash
zaproxy
```

開 GUI。第一次會問：「Do you want to persist session?」 — Yes（存 work）。

## 跟 Burp 對應

| 功能 | Burp | ZAP |
|---|---|---|
| Proxy | Proxy → Intercept | Tools → Local proxy |
| HTTP history | Proxy → HTTP history | History tab |
| Repeater | Repeater | Manual Request Editor |
| Intruder | Intruder | Fuzzer |
| Decoder | Decoder | Tools → Encode/Decode |
| Scanner | Scanner (Pro) | Active Scan |
| Spider | Crawler (Pro) | Spider |
| Extender | BApp | Marketplace |

ZAP 最大優勢：**Active Scan + 完整 CLI**。

## 使用流程

### 1. 設 browser proxy

ZAP 預設 listener: `127.0.0.1:8080`（跟 Burp 一樣，**衝突注意**）。

或在 ZAP 內：

```
Tools → Options → Local Proxies → 改 port (e.g., 8081)
```

### 2. 安裝 ZAP CA cert

```
Tools → Options → Network → Server Certificates → Generate / Save
```

下載 cert，import 到 browser。

### 3. Browse target

訪問你的 target，ZAP 自動 record。

### 4. Spider（爬整 site）

```
Right-click on target → Attack → Spider
```

ZAP 自動 follow links。

### 5. Active Scan

```
Right-click on target → Attack → Active Scan
```

自動跑 OWASP Top 10 攻擊：

- SQL injection
- XSS
- CSRF
- Path traversal
- ...

跑幾分鐘到幾小時（看 site 大小）。Alerts tab 看結果。

### 6. 看 Alert

每個 alert 含：

- Risk level（High / Medium / Low / Info）
- Description
- 建議 fix

**Pro tip**：先看 High，再 Medium，Low 多是 false positive。

## CLI / Headless mode

ZAP 強項：**完全 CLI 跑**，CI/CD 友善。

### Baseline scan（passive only）

```bash
docker run -t owasp/zap2docker-stable zap-baseline.py \
  -t https://target.example.com
```

5-10 分鐘跑完，找基本 issues（headers / cookie 設定 / redirect 問題）。**安全可在 production 跑**（passive only）。

### Full scan（active）

```bash
docker run -t owasp/zap2docker-stable zap-full-scan.py \
  -t https://target.example.com
```

慢但完整。**只在 staging / lab 跑**。

### API scan

對 REST API：

```bash
docker run -t owasp/zap2docker-stable zap-api-scan.py \
  -t https://target/openapi.json -f openapi
```

讀 OpenAPI / Swagger spec → 自動測每個 endpoint。

## CI/CD 整合範例

GitHub Actions：

```yaml
name: ZAP Baseline Scan

on: [push, pull_request]

jobs:
  zap:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Start app
        run: docker compose up -d
      
      - name: Wait for app
        run: |
          for i in {1..30}; do
            curl -f http://localhost:3000 && break || sleep 5
          done
      
      - name: ZAP Baseline Scan
        uses: zaproxy/action-baseline@v0.10.0
        with:
          target: 'http://localhost:3000'
          rules_file_name: '.zap/rules.tsv'
          allow_issue_writing: false
```

每 push / PR 自動跑 baseline。**security regression test**。

## ZAP API

ZAP 開 API：

```
Tools → Options → API → Enable
```

外部 control ZAP：

```bash
# 啟動 spider
curl 'http://localhost:8080/JSON/spider/action/scan/?url=https://target&apikey=APIKEY'

# 看 alerts
curl 'http://localhost:8080/JSON/core/view/alerts/?apikey=APIKEY'
```

寫 script 控制 ZAP 跑特定 scan / format report。

## ZAP HUD（Heads-Up Display）

ZAP 1.0 推的 in-browser UI：

```
Tools → Launch Browser → 開 Firefox
→ HUD 在 browser 內顯示 alert
```

Pen-test 時不用切 ZAP / browser，直接在 browser 看 alert。

## 一個常見誤解：「ZAP 比 Burp 弱」

**部分對**。Burp Pro 仍是 industry standard。但：

- ZAP **免費**
- ZAP automation 強很多
- 多數 pentest 場景**功能相當**
- ZAP 整合 CI/CD **更方便**

「**ZAP < Burp Pro，但 ZAP > Burp Community**」。

## 一個常見誤解：「ZAP Active Scan 在 production 安全」

**錯**。Active scan 跑大量 mutating request。可能：

- 資料污染
- 觸發 alert（公司 SOC 收到大量警報）
- 服務 down（壓測效果）

**Active scan 只在 staging / lab**。Production 用 baseline (passive) 加 manual review。

## 一個常見誤解：「ZAP 報的 alert 都對」

**錯**。Active scanner 有不少 false positive。每個 alert 要 manual confirm。

「**自動掃只是起點，重要 alert 必 manual verify**」。

## 動手練習

**1. ZAP setup + Juice Shop scan**

```bash
# 跑 Juice Shop
docker run -d -p 3000:3000 bkimminich/juice-shop

# 跑 ZAP baseline
docker run --network host -t owasp/zap2docker-stable zap-baseline.py \
  -t http://localhost:3000

# 看 report
```

**2. Active Scan via GUI**

ZAP GUI：

1. browse Juice Shop（透過 ZAP proxy）
2. Right-click target → Attack → Spider
3. Right-click target → Attack → Active Scan
4. 等 30 分鐘
5. 看 Alerts

**3. ZAP + GitHub Actions**

對自己 repo 加 ZAP baseline workflow。每 push 跑。

**4. ZAP API control**

寫 Python script 用 ZAP API：

```python
from zapv2 import ZAPv2

zap = ZAPv2(apikey='YOUR_API_KEY')
zap.urlopen('http://target')
zap.spider.scan('http://target')
# wait + active scan
```

**5. 對比 ZAP vs Burp**

對同 target 跑 ZAP active scan + Burp scanner（Pro），對比 alert 數量 / quality。

## 自我檢核

- [ ] ZAP 安裝 + setup 完成
- [ ] Spider + Active Scan 跑過至少 1 次
- [ ] CLI / Docker 模式跑過 baseline
- [ ] 知道 ZAP vs Burp 何時用哪個
- [ ] CI/CD 整合至少思考過

下一章看 sqlmap — SQL injection 自動化神器。

→ [Ch 18 sqlmap](./18-sqlmap.md)
