# Ch 6 — nmap 精通：主機發現、服務指紋、NSE 腳本

> 目標：把 nmap 用到反射動作的程度——對任何一台機器都能在 3 分鐘內跑出完整 port 清單和服務資訊。

## nmap 是什麼

nmap（Network Mapper）是網路掃描的業界標準工具。你用它：

1. **找開了哪些 port**（TCP/UDP）
2. **識別服務和版本**（HTTP？SSH？什麼版本？）
3. **跑自動化腳本**（NSE，檢查已知漏洞、枚舉資訊）

考試沒有 nmap 就沒有方向。

## OSCP 標準掃描流程

**這是你每台機器都要跑的三個指令，記起來：**

```bash
# Phase 1：全 port 快速掃（背景跑，先知道有什麼）
nmap -p- --min-rate 5000 -T4 -oN nmap/all-ports.txt 10.10.10.x

# Phase 2：針對開放 port 做詳細掃（版本 + 預設腳本）
# 假設上面掃出 22,80,443,8080
nmap -p 22,80,443,8080 -sC -sV -oN nmap/targeted.txt 10.10.10.x

# Phase 3：UDP 掃描（DNS/SNMP/TFTP 常在這）
sudo nmap -sU --top-ports 20 -oN nmap/udp.txt 10.10.10.x
```

`-oN` 把輸出存成文字檔，方便之後寫報告。

## 常用參數詳解

### 掃描類型

```bash
nmap -sS 10.10.10.x   # SYN scan（預設，半開放，需 sudo）
nmap -sT 10.10.10.x   # TCP Connect scan（不需 sudo，但更明顯）
nmap -sU 10.10.10.x   # UDP scan（要 sudo，很慢）
nmap -sN 10.10.10.x   # Null scan（繞防火牆用）
```

大多數時候用預設 SYN scan 就好。

### Port 指定

```bash
-p 80             # 只掃 port 80
-p 80,443,8080    # 掃這三個
-p 1-1000         # 掃 1 到 1000
-p-               # 掃全部 65535 個 port（重要！）
--top-ports 1000  # 掃最常用的 1000 個（預設行為）
```

**`-p-` 是關鍵**：很多靶機把服務開在非標準 port（如 8081、31337），只掃 top-1000 會漏掉。

### 服務識別

```bash
-sV               # 版本探測（重要，拿到版本才能找 exploit）
-sV --version-intensity 9  # 更積極的版本探測
```

### 腳本引擎（NSE）

```bash
-sC               # 執行預設腳本（等同 --script=default）
--script vuln     # 只跑漏洞相關腳本
--script smb-vuln-ms17-010   # 指定腳本
--script http-*              # 所有 http 開頭的腳本
```

組合常用：

```bash
-sC -sV           # 服務版本 + 預設腳本（最常用）
```

### 速度控制

```bash
-T4               # 速度等級 4（快，適合實驗室）
-T5               # 最快，可能漏 port
--min-rate 5000   # 最低每秒 5000 個 packet
```

考試環境用 `-T4` 或 `--min-rate 5000` 加速掃描，但注意某些靶機需要降速（`-T2`）才能掃準。

### 主機發現

```bash
nmap -sn 10.10.10.0/24   # ping sweep，找活著的主機
nmap -Pn 10.10.10.x      # 跳過 ping，直接掃 port（目標擋 ping 時用）
```

## NSE 腳本實用範例

### SMB 漏洞檢查

```bash
# 檢查 MS17-010（EternalBlue）
nmap -p 445 --script smb-vuln-ms17-010 10.10.10.x

# 枚舉 SMB 分享和使用者
nmap -p 445 --script smb-enum-shares,smb-enum-users 10.10.10.x

# 所有 SMB 漏洞腳本
nmap -p 139,445 --script smb-vuln-* 10.10.10.x
```

### HTTP 枚舉

```bash
# 找常見路徑
nmap -p 80 --script http-enum 10.10.10.x

# 找 HTTP 方法（PUT/DELETE 可能可以上傳）
nmap -p 80 --script http-methods 10.10.10.x

# 找 robots.txt、sitemap
nmap -p 80 --script http-robots.txt 10.10.10.x
```

### FTP

```bash
# 嘗試匿名登入
nmap -p 21 --script ftp-anon 10.10.10.x

# 找 FTP 漏洞
nmap -p 21 --script ftp-vuln-* 10.10.10.x
```

### SMTP 使用者枚舉

```bash
nmap -p 25 --script smtp-enum-users 10.10.10.x
```

## 輸出格式

```bash
-oN filename.txt  # 普通文字（人讀）
-oX filename.xml  # XML（工具讀）
-oG filename.gnmap # Grepable（grep 用）
-oA basename      # 三種格式全存（最方便）
```

養成習慣：**每次掃描都加 `-oN nmap/<描述>.txt`**，報告需要這些輸出。

## 完整範例：一台 HTB 機器的完整 nmap 流程

```bash
TARGET="10.10.10.3"
mkdir -p ~/htb/lame/nmap

# 1. 全 port 快速掃
nmap -p- --min-rate 5000 -T4 -oN ~/htb/lame/nmap/all.txt $TARGET
# 輸出：22/tcp open, 21/tcp open, 139/tcp open, 445/tcp open, 3632/tcp open

# 2. 針對開放 port 詳細掃
nmap -p 21,22,139,445,3632 -sC -sV -oN ~/htb/lame/nmap/detail.txt $TARGET

# 3. SMB 漏洞掃（因為 445 開著）
nmap -p 445 --script smb-vuln-ms17-010 -oN ~/htb/lame/nmap/smb-vuln.txt $TARGET

# 4. UDP 快速掃（時間允許的話）
sudo nmap -sU --top-ports 20 -oN ~/htb/lame/nmap/udp.txt $TARGET
```

## 解讀 nmap 輸出

```
PORT     STATE  SERVICE   VERSION
21/tcp   open   ftp       vsftpd 2.3.4     ← 版本！搜 vsftpd 2.3.4 exploit
22/tcp   open   ssh       OpenSSH 4.7p1    ← 舊版，有沒有 CVE？
139/tcp  open   netbios-ssn Samba smbd 3.X  ← Samba 版本
445/tcp  open   netbios-ssn Samba smbd 3.0.20 ← 更精確的版本
3632/tcp open   distccd   distccd v1       ← 不熟悉的服務，searchsploit
```

**看到版本號就去 searchsploit 搜**——這是 Ch 9 的重點。

## 防火牆繞過技巧

有些靶機有防火牆，普通掃描會全部顯示 filtered：

```bash
# 調整 TTL（模擬特定 OS）
nmap --ttl 128 10.10.10.x

# Fragment packets（碎片化，繞過簡單防火牆）
nmap -f 10.10.10.x

# 用 UDP 掃（有些防火牆只過濾 TCP）
sudo nmap -sU 10.10.10.x

# 降速（避免速率限制）
nmap -T2 10.10.10.x
```

## 本章對應靶機

- **HTB Lame**（Easy）：練習全流程，看看 vsftpd 2.3.4 和 Samba 版本
- **HTB Legacy**（Easy）：Windows，看 SMB vuln 腳本的輸出
- **HTB Blue**（Easy）：Windows，確認 MS17-010 的掃描結果

三台都先用 nmap 跑出完整資訊，先不要打——枚舉練習在 Practice A。

## 自我檢核

- [ ] 能背出 OSCP 標準三段 nmap 流程
- [ ] 知道 `-p-` 和 `--top-ports 1000` 的差別，以及為什麼 `-p-` 重要
- [ ] 能用 NSE 腳本掃 SMB 漏洞
- [ ] 每次掃描都有加 `-oN` 存輸出

→ [Ch 7 服務枚舉：SMB / FTP / SSH / SNMP / DNS](./07-service-enumeration.md)
