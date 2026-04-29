# OWASP Top 10 + Web 安全完整課程

> 給有 binary security / pentest 背景但 web 不熟、想徹底學會 OWASP Top 10 + API 安全 + 防禦產品 + 真實 CVE 案例 + 紅藍隊演習的工程師。

這系列把 web 安全從零教完：HTTP / Browser 模型 → **OWASP Top 10 2025** 全部 10 個（含 2021→2025 對照）→ 主流工具（Burp / sqlmap / ZAP / nuclei）→ API Security Top 10 2023 → WAF / RASP 防禦 → 真實 CVE 案例（Log4Shell / Heartbleed / SolarWinds 等）→ 紅藍隊演習方法論。讀完能 audit 一個 web app 找出 OWASP 弱點 + 知道怎麼修 + 對 API 安全有概念 + 看得懂 CVE advisory。

> **2025 版本說明**：本系列章節順序與編號採用 **OWASP Top 10:2025 release candidate**（2025-11-06 公開）。RC 與最終版差異預期極小；多數企業 / compliance 仍同時參考 2021，因此每章開頭會點明「2021 → 2025 變動」，並有總對照表（下方）。

## 為什麼學這個？

- **Web 是攻擊面最大的領域**：90% 真實 breach 從 web app 起
- **OWASP Top 10 是業界共通語言**：dev / sec / mgmt 都在用，講不出 OWASP 像不在這行
- **理解攻擊才能設計防禦**：學完不只 pentester 視角，dev 也能寫出更安全的 code
- **跟現實 CVE 對接**：每個 OWASP 都對應到真實大事件，學完看 CVE advisory 不再霧裡看花

## 一個必須先講清楚的事

這課**不是**「教你怎麼非法滲透別人 server」。所有實作都在：

- OWASP Juice Shop / DVWA / WebGoat（合法 vulnerable web app）
- 自架 lab
- 你自己有授權的 target
- bug bounty 範圍內的 site

**未授權對別人 server 攻擊在多數國家是刑事犯罪**。這課教的工具跟技術是雙面刃 — 用來做防禦或合法 pentest，不要用來做違法的事。

## 2021 → 2025 對照表

| 2025 | 名稱 | 2021 對應 | 主要變動 |
|---|---|---|---|
| A01 | Broken Access Control | A01 + A10 | **吸收 SSRF** |
| A02 | Security Misconfiguration | A05 | 從 #5 升到 #2（cloud / IaC 攻擊面爆量） |
| A03 | Software Supply Chain Failures | A06 | 從 Vulnerable Components **擴張**為整條 supply chain |
| A04 | Cryptographic Failures | A02 | 位置降，本身範圍不變 |
| A05 | Injection | A03 | 編號變、含 XSS / SQLi / Command / SSTI 等 |
| A06 | Insecure Design | A04 | 編號變，本質同 |
| A07 | Authentication Failures | A07 | **改名**（拿掉「Identification &」） |
| A08 | Software or Data Integrity Failures | A08 | 沒變（與 A03 部分重疊） |
| A09 | Security Logging & Alerting Failures | A09 | **「Monitoring」改 「Alerting」** |
| A10 | Mishandling of Exceptional Conditions | — | **全新類別** |

退出：2021 的 A10 SSRF（併入 A01）。
新增：2025 的 A10 Mishandling of Exceptional Conditions。

## 課程地圖

### Part 1 — Web 基礎
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 HTTP 完整版](./01-http-complete.md)
- [Ch 2 Web 架構速覽](./02-web-architecture.md)
- [Ch 3 Browser 安全模型](./03-browser-security-model.md)

### Part 2 — OWASP Top 10 2025
- [Ch 4 A01 Broken Access Control（含 SSRF）](./04-a01-broken-access-control.md)
- [Ch 5 A02 Security Misconfiguration](./05-a02-misconfiguration.md)
- [Ch 6 A03 Software Supply Chain Failures](./06-a03-software-supply-chain.md)
- [Ch 7 A04 Cryptographic Failures](./07-a04-cryptographic-failures.md)
- [Ch 8 A05 SQL Injection 深入](./08-a05-sql-injection.md)
- [Ch 9 A05 XSS](./09-a05-xss.md)
- [Ch 10 A05 其他注入 (Command/NoSQL/LDAP/SSTI/XXE)](./10-a05-other-injections.md)
- [Ch 11 A06 Insecure Design](./11-a06-insecure-design.md)
- [Ch 12 A07 Authentication Failures](./12-a07-auth-failures.md)
- [Ch 13 A08 Software & Data Integrity Failures](./13-a08-integrity-failures.md)
- [Ch 14 A09 Security Logging & Alerting Failures](./14-a09-logging-alerting-failures.md)
- [Ch 15 A10 Mishandling of Exceptional Conditions](./15-a10-mishandling-exceptions.md)

### Part 3 — 主流工具
- [Ch 16 Burp Suite 完整](./16-burp-suite.md)
- [Ch 17 OWASP ZAP](./17-owasp-zap.md)
- [Ch 18 sqlmap](./18-sqlmap.md)
- [Ch 19 其他工具大全 (nikto/dirb/wpscan/nuclei/ffuf)](./19-other-tools.md)
- [練習 A：攻 OWASP Juice Shop](./practice-a-juice-shop.md)

### Part 4 — OWASP API Security Top 10 2023
- [Ch 20 API 安全 + API1-3](./20-api-top10-part1.md)
- [Ch 21 API4-7](./21-api-top10-part2.md)
- [Ch 22 API8-10](./22-api-top10-part3.md)
- [練習 B：API pentesting](./practice-b-api-pentest.md)

### Part 5 — 防禦、案例與組織
- [Ch 23 完整 CVE 案例研究](./23-cve-case-studies.md)
- [Ch 24 WAF / RASP 防禦產品深入](./24-waf-rasp-defenses.md)
- [Ch 25 安全 SDLC + threat modeling](./25-secure-sdlc.md)
- [Ch 26 Bug bounty 心法 + responsible disclosure](./26-bug-bounty.md)
- [Ch 27 紅藍隊演習方法論](./27-red-blue-team.md)

### Final Project
- [Final Project：完整 web app pentest report](./final-project-pentest-report.md)

## 學習方式建議

1. **每章都要在 Juice Shop / DVWA 裡動手**：純讀沒用，每個 vuln 要親手 exploit + fix
2. **學防禦角度**：每讀完一個攻擊技巧，問「我寫 code 怎麼避免」
3. **看真實 CVE writeup**：HackerOne / Bug Bounty 公開 reports 是最棒的學習材料
4. **法律意識**：未授權的攻擊永遠別做，bug bounty 也要照 scope

## 參考資料

- 《The Web Application Hacker's Handbook》— 經典中的經典
- OWASP 官方 Top 10：https://owasp.org/Top10/
- OWASP Top 10:2025 RC：https://owasp.org/Top10/2025/
- OWASP API Security Top 10：https://owasp.org/API-Security/
- OWASP Cheat Sheet Series：https://cheatsheetseries.owasp.org/
- PortSwigger Web Security Academy：https://portswigger.net/web-security （免費 lab）
- HackerOne Hacktivity：https://hackerone.com/hacktivity（看別人 disclosed reports）
- MITRE ATT&CK：https://attack.mitre.org/
