# Ch 0 — 環境搭建

> 目標：把 web pentest 工具裝齊、跑起 vulnerable lab，後面每章都靠這套。

## 必裝工具

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install -y \
  curl wget \
  git \
  python3 python3-pip \
  nodejs npm \
  docker.io docker-compose \
  nmap nikto \
  whatweb \
  hashcat john \
  ffuf gobuster

# Burp Suite Community Edition
# 從 https://portswigger.net/burp/communitydownload 下載

# OWASP ZAP
# https://www.zaproxy.org/download/

# sqlmap
sudo apt install sqlmap   # 或 pip install sqlmap

# nuclei
go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
nuclei -update-templates
```

## Browser 端

- **Firefox**（推薦，dev tools 強大）
- **Burp Suite Browser**（內建 proxy，免設）

裝 Firefox extensions：

- **FoxyProxy Standard**（快速切 proxy）
- **Cookie-Editor**（編輯 cookie）
- **Wappalyzer**（看網站用什麼技術）
- **HackTools**（payload generator）

## Vulnerable Lab 環境

整課實作都在這些 lab 跑。**不要對真 site 攻擊**。

### 1. OWASP Juice Shop（首推）

現代 SPA + REST API 風格，最完整的 OWASP Top 10 lab：

```bash
# Docker（最簡單）
docker run -d -p 3000:3000 --name juice-shop bkimminich/juice-shop

# 開瀏覽器
firefox http://localhost:3000
```

**100+ 挑戰**，從入門到 advanced 都有。對應 OWASP Top 10 + API Top 10。

### 2. DVWA（Damn Vulnerable Web Application）

老牌 PHP 應用，UI 老但 vuln 經典：

```bash
docker run -d -p 8080:80 --name dvwa vulnerables/web-dvwa

firefox http://localhost:8080
# admin / password
```

3 個難度（low / medium / high）幫助你看「**爛 code → 普通 code → 較好 code**」對比。

### 3. WebGoat（OWASP 官方教材）

OWASP 官方的「**邊學邊解**」app：

```bash
docker run -d -p 8080:8080 -p 9090:9090 webgoat/goatandwolf

firefox http://localhost:8080/WebGoat
```

每課題目 + 提示 + lesson explained。**新手最友善**。

### 4. PortSwigger Web Security Academy（線上）

不用裝任何東西，**免費**。Burp Suite 公司提供：

```
https://portswigger.net/web-security
```

200+ labs，講解 + 互動 lab 一條龍。**這資源是金本位**，很多 pentester 從這入門。

## Burp Suite 初步設定

1. 開啟 Burp Suite Community
2. Proxy → Options → 確認 listener 在 127.0.0.1:8080
3. Browser 設 proxy 127.0.0.1:8080（用 FoxyProxy）
4. 訪問任何 HTTPS site → 第一次會跳憑證警告
5. 訪問 `http://burp` → 下載 Burp 的 CA cert
6. Browser import CA → 之後 HTTPS 不再警告

```bash
# 確認 Burp 在跑
curl --proxy http://127.0.0.1:8080 -k https://example.com
# Burp Proxy → HTTP history 看到 request
```

## OWASP ZAP（Burp 替代）

開源、免費。CI/CD 整合好：

```bash
# Linux GUI
zaproxy

# Headless / CI
zap-baseline.py -t https://target.com
```

跟 Burp 互補：

- **Burp Suite**：互動 / 手動 pentest 強
- **ZAP**：自動化 / CI 強

學會兩個都會。

## 目錄結構建議

每個練習 / lab 自建資料夾保留 notes：

```
~/owasp-lab/
├── juice-shop/
│   ├── notes.md
│   ├── attack-payloads/
│   └── screenshots/
├── dvwa/
└── practice-a/
    └── pentest-report.md
```

## 一個常見踩雷：拿 vulnerable lab 真去攻擊

**不要**對真 site 攻擊，**即使你覺得沒影響**。

- 「**測試**」也是攻擊
- 自動掃描器留 log，IP 對得到你
- 即使 0 漏洞被發現，你已經違法

合法測試只在：

- 自己 own 的 server
- bug bounty 程式 scope 內
- 簽合約的 pentest 客戶
- vulnerable lab（Juice Shop 等）

## 一個常見踩雷：Burp Suite Community 沒 Intruder rate limit

Community 版的 Intruder 有 throttle（attack 慢）。專業版貴（每年 $400+）。

替代：

- 用 PortSwigger Academy（免費，line speed）
- 用 sqlmap / ffuf 等專門工具
- ZAP fuzzer（無 throttle）

## Sanity check

```bash
# Juice Shop 跑著
curl http://localhost:3000

# DVWA 跑著
curl -I http://localhost:8080

# Burp Proxy 在
nc -zv 127.0.0.1 8080

# sqlmap 能跑
sqlmap --version

# nuclei 能跑
nuclei -version
```

全 OK 就過關。

## 自我檢核

- [ ] Juice Shop / DVWA 至少 1 個跑著
- [ ] Burp Suite 設好 + browser proxy 通
- [ ] sqlmap / nuclei 等工具能跑
- [ ] 知道 PortSwigger Academy 是免費寶藏
- [ ] 法律警告記住

下一章看 HTTP 完整版 — web 安全的基礎。

→ [Ch 1 HTTP 完整版](./01-http-complete.md)
