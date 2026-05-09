# Ch 30 — AD 基礎：Domain / Forest / Kerberos / LDAP

> 目標：建立 Active Directory 的基本概念——Domain、使用者/群組結構、Kerberos 認證流程，為後面的攻擊技術鋪底。

## 為什麼 AD 值得單獨學

OSCP 考試有 40 分來自 AD 三機器鏈。AD 不懂，這 40 分幾乎拿不到。

更重要的是，**現實世界 95% 以上的企業都用 AD**。OSCP 之後你能接觸到的滲透測試，幾乎都包含 AD 環境。

## Active Directory 的結構

```
Forest（樹系）
└── Domain（網域）— corp.local
    ├── Domain Controller (DC) — 管理整個 Domain 的伺服器
    ├── Organizational Unit (OU) — 管理用的容器
    ├── Users（使用者帳號）
    ├── Groups（群組）
    │   ├── Domain Admins  ← 接管 DC 的目標
    │   └── Enterprise Admins（Forest 層級）
    └── Computers（加入 Domain 的電腦）
```

### 關鍵概念

**Domain Controller（DC）**：儲存所有 AD 資訊的伺服器。OSCP 三機器鏈的最終目標就是 DC。

**Domain Admins**：這個群組的成員對整個 Domain 有完全控制。拿到 DA 就是接管 AD。

**SID（Security Identifier）**：每個物件的唯一識別碼。  
Domain SID：`S-1-5-21-<X>-<Y>-<Z>`  
常見的 Well-known SID：`S-1-5-21-...-500`（Administrator）

## Kerberos 認證

AD 的主要認證協定。理解它才能理解 Kerberoasting 和 Pass-the-Ticket。

```
使用者想存取服務（如 File Server）的流程：

1. 使用者 → DC：我是 alice，我想要 TGT
   （用 alice 的密碼 hash 加密）

2. DC → 使用者：這是你的 TGT
   （Ticket Granting Ticket，用 krbtgt 帳號的 key 加密）

3. 使用者 → DC：我有 TGT，我想要 ST（訪問 FileServer 的票）

4. DC → 使用者：這是 Service Ticket
   （用 FileServer 服務帳號的密碼 hash 加密）

5. 使用者 → FileServer：我有這個 ST，讓我進去

6. FileServer：驗證 ST 合法 → 放行
```

關鍵點：
- **TGT**：你的「通行證」，有它才能請求服務票
- **Service Ticket（ST/TGS）**：特定服務的訪問憑證，用服務帳號 hash 加密
- **Kerberoasting**：把 ST 帶回來離線破解，因為 ST 是用服務帳號 hash 加密的（Ch 32）

## LDAP

LDAP（Lightweight Directory Access Protocol）是查詢 AD 的協定，port 389/636。

```bash
# 查詢所有使用者
ldapsearch -x -H ldap://10.10.10.x -b "dc=corp,dc=local" "(objectClass=user)"

# 匿名查詢（Null Bind，舊 AD 可能允許）
ldapsearch -x -H ldap://10.10.10.x -b "dc=corp,dc=local"

# 帶帳號查詢
ldapsearch -x -H ldap://10.10.10.x -D "user@corp.local" -w "password" -b "dc=corp,dc=local"
```

## SMB 在 AD 的角色

AD 環境中 SMB（port 445）很重要：
- 用 SMB 連到 DC：`\\DC\SYSVOL`（Group Policy）
- PtH（Pass-the-Hash）透過 SMB 橫向移動
- Remote WMI / WinRM 用 SMB 認證

## 常見攻擊面概覽

```
初始立足：
  AS-REP Roasting  → 不需要預認證的帳號
  Password Spray   → 弱密碼
  LLMNR Poisoning  → 網路廣播中毒（Responder）

內部擴散：
  Kerberoasting    → 服務帳號 hash
  BloodHound      → 找攻擊路徑
  ACL 濫用         → 不當的權限設定

橫向移動：
  Pass-the-Hash   → 用 NTLM hash 連接
  Pass-the-Ticket → 用 Kerberos TGT

DC 接管：
  DCSync           → 模擬 DC 同步，提取所有 hash
  Golden Ticket    → 偽造 TGT，永久後門
```

## 環境偵測：我在 AD 環境嗎？

```cmd
# 看是否有 Domain
systeminfo | findstr /i "domain"
net config workstation

# 找 DC
nltest /dclist:corp.local
nslookup -type=SRV _ldap._tcp.dc._msdcs.corp.local

# LDAP 是否開著
nmap -p 389,636 10.10.10.x
```

## PowerShell AD 模組

```powershell
# Import-Module ActiveDirectory（需要安裝，DC 上通常有）

# 找所有 DA
Get-ADGroupMember "Domain Admins"

# 找所有使用者
Get-ADUser -Filter *

# 找有 SPN 的服務帳號（Kerberoasting 目標）
Get-ADUser -Filter {ServicePrincipalName -ne "$null"} -Properties ServicePrincipalName
```

## 工具準備

AD 攻擊需要以下工具（Kali 預裝或需要安裝）：

```bash
# impacket（核心工具集）
pip3 install impacket
# 或
git clone https://github.com/fortra/impacket.git

# BloodHound（攻擊路徑視覺化）
sudo apt install bloodhound

# CrackMapExec（橫向移動）
pip3 install crackmapexec
# 或
sudo apt install crackmapexec
```

## 本章對應靶機

- **HTB Forest**：AD 環境的完整入門機器，AS-REP Roasting + DCSync

## 自我檢核

- [ ] 能說出 TGT 和 Service Ticket 的差別
- [ ] 知道 Kerberoasting 是利用哪個票據（ST/TGS）
- [ ] 知道 Domain Admins 群組的意義
- [ ] 能用 nmap 確認目標是否是 DC（LDAP port 開著）

→ [Ch 31 初始立足：Password Spray / AS-REP Roasting](./31-ad-initial-access.md)
