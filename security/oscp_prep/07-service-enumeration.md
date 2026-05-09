# Ch 7 — 服務枚舉：SMB / FTP / SSH / SNMP / DNS

> 目標：對每個常見服務，有一套標準的枚舉工具和指令，拿到版本號、使用者、分享、設定資訊。

## 為什麼服務枚舉單獨一章

nmap 告訴你「port 445 開著，Samba 3.0.20」，但接下來你要做的是**深入挖這個服務**：

- FTP：匿名登入？有沒有可讀/可寫目錄？
- SMB：哪些分享？有沒有空密碼帳號？
- SNMP：能不能讀到系統資訊、介面資訊、路由表？

工具比 nmap NSE 更針對，輸出更詳細。

## SMB 枚舉

SMB（port 139/445）是 Windows 環境最重要的服務，也是 OSCP 最高頻出現的攻擊向量。

### smbclient

```bash
# 列出所有分享（-N = 不提示輸入密碼，嘗試 Null session）
smbclient -L //10.10.10.x -N

# 連進特定分享
smbclient //10.10.10.x/sharename -N

# 連進後的操作：
#   ls          → 列目錄
#   get file    → 下載
#   put file    → 上傳
#   cd dir      → 換目錄
```

### enum4linux

老工具，但 OSCP 常用：

```bash
enum4linux -a 10.10.10.x
```

這個指令會嘗試：
- 使用者枚舉（-U）
- 分享枚舉（-S）
- 密碼政策（-P）
- 群組（-G）
- OS 資訊（-o）

輸出很長，找關鍵字：`Username`, `Share`, `Password Policy`。

### enum4linux-ng（新版）

```bash
pip3 install enum4linux-ng
enum4linux-ng -A 10.10.10.x
```

### smbmap

```bash
# 更直觀地看分享和權限
smbmap -H 10.10.10.x
smbmap -H 10.10.10.x -u null

# 用帳密連
smbmap -H 10.10.10.x -u username -p password
```

### nmap SMB 腳本

```bash
# 全套 SMB 枚舉
nmap -p 445 --script smb-enum-shares,smb-enum-users,smb-os-discovery 10.10.10.x

# 漏洞掃描
nmap -p 445 --script smb-vuln-ms17-010,smb-vuln-ms08-067 10.10.10.x
```

## FTP 枚舉

### 匿名登入

```bash
ftp 10.10.10.x
# Username: anonymous
# Password: anonymous 或任意 email

# 或用 curl
curl -v ftp://10.10.10.x --user anonymous:anonymous

# nmap 腳本
nmap -p 21 --script ftp-anon 10.10.10.x
```

### 進去後要做什麼

```bash
# FTP 互動模式
ls -la        # 列出所有（含隱藏）
pwd           # 當前目錄
get file.txt  # 下載
put local.sh  # 上傳（如果有寫權限，可以放 webshell）
binary        # 切換到二進位模式（傳 exe/zip 前要切）
```

**重點**：如果 FTP 的目錄和 Web 根目錄是同一個，能上傳 `.php` webshell 就能 RCE。

### FTP 版本漏洞

```bash
# vsftpd 2.3.4 有後門（直接反彈 shell）
searchsploit vsftpd 2.3.4

# ProFTPD 有路徑穿越和任意讀取漏洞
searchsploit proftpd
```

## SSH 枚舉

SSH 本身很難直接攻，但有幾個點：

### 版本確認

```bash
# 看 SSH 版本
nmap -p 22 -sV 10.10.10.x
# 或直接 banner grab
nc 10.10.10.x 22
```

OpenSSH < 7.7 有使用者枚舉漏洞（CVE-2018-15473）：

```bash
searchsploit openssh user enumeration
# 可以確認帳號是否存在
```

### SSH Key 問題

```bash
# 如果靶機的 id_rsa 私鑰可讀（設定錯誤或你已有 shell）
cat /home/user/.ssh/id_rsa

# 拿到後在 Kali：
chmod 600 stolen_id_rsa
ssh -i stolen_id_rsa user@10.10.10.x
```

### Hydra 暴力破解（最後手段）

```bash
hydra -l username -P /usr/share/wordlists/rockyou.txt ssh://10.10.10.x
# OSCP 考試謹慎使用暴力破解，很慢而且可能讓機器不穩
```

## SNMP 枚舉

SNMP（Simple Network Management Protocol，UDP 161）常被忽略，但資訊量很大。

### 確認 SNMP 開著

```bash
sudo nmap -sU -p 161 10.10.10.x
```

### onesixtyone（掃 community string）

預設 community string 是 `public`，很多設備還在用：

```bash
onesixtyone -c /usr/share/doc/onesixtyone/dict.txt 10.10.10.x
```

### snmpwalk（枚舉所有資訊）

```bash
# 用 public community string 讀所有 OID
snmpwalk -v2c -c public 10.10.10.x

# 只看系統資訊
snmpwalk -v2c -c public 10.10.10.x 1.3.6.1.2.1.1

# 只看路由表
snmpwalk -v2c -c public 10.10.10.x 1.3.6.1.2.1.4.21

# 只看使用者帳號（Windows 常用）
snmpwalk -v2c -c public 10.10.10.x 1.3.6.1.4.1.77.1.2.25
```

SNMP 洩漏的資訊可能包含：系統版本、網路介面、路由、執行中的程序、使用者名稱——全都是後續攻擊的情報。

## DNS 枚舉

### 基本查詢

```bash
# 正查詢
nslookup target.com 10.10.10.x    # 指定 DNS 伺服器查
dig @10.10.10.x target.com

# 反向查詢
dig -x 10.10.10.x
nslookup 10.10.10.x

# MX 記錄（郵件伺服器）
dig @10.10.10.x target.com MX

# 所有記錄
dig @10.10.10.x target.com ANY
```

### Zone Transfer（高價值目標）

```bash
# 嘗試 Zone Transfer（如果成功，拿到所有子域名）
dig axfr @10.10.10.x target.htb

# dnsrecon
dnsrecon -d target.htb -t axfr -n 10.10.10.x
```

Zone Transfer 成功很少見，但一旦成功，子域名清單就是新的攻擊面。

### 子域名暴力列舉

```bash
# gobuster（DNS 模式）
gobuster dns -d target.htb -w /usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt

# dnsrecon
dnsrecon -d target.htb -t brt -D /usr/share/seclists/Discovery/DNS/common.txt
```

## SMTP 使用者枚舉

```bash
# 連進去手動試
nc 10.10.10.x 25
VRFY root          # 確認使用者是否存在
EXPN maillist      # 展開郵件列表

# nmap 腳本
nmap -p 25 --script smtp-enum-users --script-args smtp-enum-users.methods={VRFY,EXPN,RCPT} 10.10.10.x
```

## 整合工具：AutoRecon

```bash
# 自動對目標跑所有枚舉工具（比你手動快）
pip3 install autorecon
autorecon 10.10.10.x
```

AutoRecon 背景跑 nmap、gobuster、enum4linux、snmpwalk 等，適合考試時同時枚舉多台機器。不過要理解工具在做什麼，不能只會看輸出。

## 本章對應靶機

| 機器 | 重點服務 |
|------|---------|
| HTB Lame | SMB（Samba 3.0.20），enum4linux |
| HTB Forest | SMB + DNS，AD 環境的 DNS 枚舉 |
| HTB Grandpa | HTTP（IIS 6.0 WebDAV），FTP |

## 自我檢核

- [ ] 能用 `smbclient -L` 列出分享，並連進去下載檔案
- [ ] 知道 `enum4linux -a` 能拿到哪些資訊
- [ ] 能用 `snmpwalk` 讀 SNMP，知道哪個 OID 是使用者帳號
- [ ] 嘗試過 `dig axfr` Zone Transfer（就算失敗也要知道怎麼試）

→ [Ch 8 Web 情報蒐集：目錄爆破、vhost、技術指紋](./08-web-recon.md)
