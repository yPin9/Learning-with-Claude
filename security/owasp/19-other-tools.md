# Ch 19 — 其他工具大全：nikto / dirb / wpscan / nuclei / ffuf / 等

> 目標：認識 6+ 個常用 web pentest 工具，知道何時用哪個。

## 工具分類

| 用途 | 工具 |
|---|---|
| 全面掃 | nikto, ZAP baseline |
| 目錄 / 檔案爆破 | dirb, gobuster, ffuf, dirsearch |
| WordPress | wpscan |
| Template-based 漏洞掃 | nuclei |
| Subdomain 列舉 | sublist3r, amass, subfinder |
| API / fuzzing | ffuf, wfuzz |
| Brute force | hydra, medusa |
| Hash crack | hashcat, john |

## 1. nikto — 老牌 web scanner

```bash
nikto -h https://target.com
```

掃常見：

- 預設配置
- 過時 software（Apache 老版）
- 危險檔案（`/admin/`, `.git/`, backup）
- 已知 CVE（如 Heartbleed）
- HTTP headers 缺

特點：

- 老（perl 寫的）但維護中
- 規則庫不算最新
- noisy（容易被 WAF 擋）

新場景多用 nuclei，**nikto 仍是經典 baseline**。

## 2. gobuster / dirb / dirsearch / ffuf — 目錄爆破

「**找隱藏的 path**」 — admin / backup / `.git` / config 等。

### gobuster

```bash
gobuster dir -u https://target.com -w /usr/share/wordlists/dirb/common.txt
```

選項：

- `-x php,html,txt` 試這些 extension
- `-t 50` thread 數
- `-r` follow redirect
- `-k` ignore SSL

### dirb

老牌但簡單：

```bash
dirb https://target.com /usr/share/wordlists/dirb/common.txt
```

### dirsearch（更強）

```bash
dirsearch -u https://target.com -e php,html,txt
```

支援 advanced filter / status code 排除 / 等。

### ffuf（最快、最 flexible）

```bash
# 目錄爆破
ffuf -u https://target.com/FUZZ -w wordlist.txt

# subdomain
ffuf -u https://FUZZ.target.com -w subdomains.txt

# parameter fuzzing
ffuf -u "https://target.com/api?FUZZ=value" -w params.txt -mc 200,500

# auth brute force
ffuf -u "https://target.com/login" \
     -X POST -d "user=admin&pass=FUZZ" \
     -w passwords.txt -fr "Login failed"
```

ffuf 是「**HTTP fuzzer 瑞士刀**」。新 pentester 必學。

### Wordlists

常用：

- `/usr/share/wordlists/dirb/common.txt`
- **SecLists**（GitHub `danielmiessler/SecLists`）— 最完整

```bash
git clone https://github.com/danielmiessler/SecLists.git
```

## 3. wpscan — WordPress

WordPress 攻擊面大（plugin / theme 老）：

```bash
wpscan --url https://target.com
wpscan --url https://target.com --enumerate u   # 列 user
wpscan --url https://target.com --enumerate p   # plugin
wpscan --url https://target.com --enumerate t   # theme

# 字典攻擊
wpscan --url https://target.com -U admin -P passwords.txt
```

需要 API key（free 25 query/day）：https://wpscan.com/api

## 4. nuclei — Template-based scanner

「**用 YAML template 描述漏洞**，工具自動跑」。

```bash
# 跑全部 templates
nuclei -u https://target.com

# 只跑 CVE templates
nuclei -t ~/nuclei-templates/cves/ -u https://target.com

# 只跑 OWASP Top 10
nuclei -t ~/nuclei-templates/vulnerabilities/ -u https://target.com

# 只跑 misconfig
nuclei -t ~/nuclei-templates/misconfiguration/ -u https://target.com

# 多個 target
nuclei -l urls.txt
```

Templates 由社群維護，**8000+ templates**：

```bash
nuclei -update-templates
```

modern web pentest 的「**第一招**」 — 5 分鐘確認 known CVE / common misconfig。

### 寫自己 template

```yaml
id: my-custom-check
info:
  name: Check for /admin
  severity: medium

requests:
  - method: GET
    path:
      - "{{BaseURL}}/admin"
    matchers:
      - type: status
        status:
          - 200
```

公司內部 / 客戶特定漏洞寫 template，自動化掃。

## 5. Subdomain enumeration

找 subdomain → 擴大攻擊面（可能 admin.example.com 沒 patch）：

```bash
# subfinder
subfinder -d example.com

# amass (慢但完整)
amass enum -d example.com

# sublist3r
sublist3r -d example.com

# ffuf
ffuf -u https://FUZZ.example.com -w subdomains.txt
```

passive (查公開 source) vs active (DNS brute force)：

- passive：safer, 不 touching target DNS
- active：找 obscure subdomain（內部）

## 6. hydra — Brute force（網路 service）

「**對任何 protocol brute force**」：

```bash
# SSH
hydra -l admin -P passwords.txt ssh://target

# HTTP form
hydra -l admin -P passwords.txt target http-post-form \
  "/login.php:user=^USER^&pass=^PASS^:Login failed"

# FTP
hydra -L users.txt -P passwords.txt ftp://target

# RDP / VNC / SMB / MySQL / etc
```

20+ 種 service 支援。

## 7. hashcat / john — hash 破解

```bash
# 從 SQL injection dump 拿 hash
echo "5f4dcc3b5aa765d61d8327deb882cf99" > hash.txt   # md5("password")

# john
john --wordlist=rockyou.txt hash.txt --format=raw-md5
john --show hash.txt --format=raw-md5

# hashcat (GPU)
hashcat -a 0 -m 0 hash.txt rockyou.txt
```

`-m` mode：

- 0 = MD5
- 100 = SHA-1
- 1400 = SHA-256
- 1800 = SHA-512 crypt
- 3200 = bcrypt
- 16500 = JWT

bcrypt / scrypt / Argon2 慢到實用上不能 brute force（per design）。

## 8. amass / subfinder — 已 cover

## 9. SecLists — wordlists

GitHub `danielmiessler/SecLists`，**所有** pentester wordlist 集合：

- usernames
- passwords (rockyou, common, top-N)
- payloads (XSS, SQL, command injection)
- Discovery (directory, subdomain)
- Fuzzing (special chars, format strings)

每個 wordlist 都有用。

## 10. CyberChef — encode / decode 神器

「**web 上的 swiss army knife**」：

```
https://gchq.github.io/CyberChef/
```

URL encode / base64 / hash / regex / decompress / encrypt — 拖拉式組合 recipe。

## 工具選擇 cheatsheet

```
我需要 ...                    用什麼
─────────────────             ────────
看 site 哪些 path 開           gobuster / ffuf / dirsearch
找 subdomain                   subfinder / amass
掃 known CVE                   nuclei
測 SQL injection               sqlmap
測 WordPress                   wpscan
mass scan                       nmap (port) + nuclei (web)
Brute force login              hydra (service) / Burp Intruder (web form)
解 hash                        hashcat (GPU) / john (CPU)
encode / decode                CyberChef
```

## 動手練習

**1. nuclei 對自己 site**

```bash
nuclei -u http://localhost:3000   # Juice Shop
```

看找到什麼。

**2. ffuf brute force / fuzz**

```bash
# 對 Juice Shop 找 hidden path
ffuf -u "http://localhost:3000/FUZZ" -w /usr/share/wordlists/dirb/common.txt -mc 200,301,302
```

**3. wpscan 對 WordPress**

```bash
docker run -d -p 8080:80 wordpress:latest
wpscan --url http://localhost:8080
```

**4. hashcat 練習**

```bash
# 自己 hash 自己破
echo -n "password123" | md5sum
# 5f4dcc...

echo "5f4dcc..." > h.txt
hashcat -a 0 -m 0 h.txt /usr/share/wordlists/rockyou.txt
```

**5. SecLists 探索**

```bash
git clone https://github.com/danielmiessler/SecLists.git
ls SecLists/
```

挑 5 個有趣 wordlist，實際用 1 個跑 ffuf。

## 自我檢核

- [ ] 知道 6+ 工具各自定位
- [ ] ffuf / gobuster 跑過至少 1 次
- [ ] nuclei 對自己 site 跑過
- [ ] hashcat / john 練過破解
- [ ] 知道 SecLists 是寶藏
- [ ] CyberChef 解過 encoding 問題

Part 3 結束。練習 A 整合所有工具攻 Juice Shop。

→ [練習 A：攻 OWASP Juice Shop](./practice-a-juice-shop.md)
