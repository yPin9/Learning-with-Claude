# Ch 8 — Web 情報蒐集：目錄爆破、vhost、技術指紋

> 目標：看到一個 Web 服務，能系統性地找出隱藏路徑、虛擬主機、技術棧，為 Part 3 的漏洞利用做準備。

## Web 服務是 OSCP 的高頻入口

靶機開了 port 80/443，你不能只是打開瀏覽器看看首頁就結束。Web 枚舉要做的事：

```
1. 技術指紋（用什麼框架？什麼版本？）
2. 目錄爆破（有沒有隱藏的管理頁面？）
3. 虛擬主機（vhost）枚舉
4. robots.txt、備份檔、原始碼洩漏
5. 表單和參數初探
```

## 先看基礎資訊

```bash
# 先在瀏覽器開，手動看
# 然後 curl 看 HTTP header
curl -I http://10.10.10.x
curl -I https://10.10.10.x -k   # -k 忽略憑證錯誤

# 輸出範例：
HTTP/1.1 200 OK
Server: Apache/2.4.29 (Ubuntu)   ← 版本資訊！
X-Powered-By: PHP/7.2.24         ← PHP 版本！
Set-Cookie: PHPSESSID=abc123     ← Session 機制
```

看 HTTP Header 能拿到：伺服器類型、版本、後端語言，直接餵給 searchsploit。

```bash
# 看更多 response 內容
curl -v http://10.10.10.x 2>&1 | head -50

# 看原始碼的 meta / link 標籤
curl -s http://10.10.10.x | grep -i "generator\|powered\|version\|framework"

# robots.txt（常有管理路徑）
curl http://10.10.10.x/robots.txt

# sitemap
curl http://10.10.10.x/sitemap.xml
```

## 目錄爆破

**沒有爆破就等於沒做 Web 枚舉。**

### gobuster

```bash
# 基本目錄爆破
gobuster dir -u http://10.10.10.x -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt

# 加上副檔名（找 .php .txt .bak .config）
gobuster dir -u http://10.10.10.x \
    -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt \
    -x php,txt,bak,config,xml,zip

# 加上 HTTP 標頭（某些應用需要）
gobuster dir -u http://10.10.10.x \
    -w /usr/share/wordlists/dirb/common.txt \
    -H "Host: target.htb"
```

### ffuf（更快更靈活）

```bash
# 目錄爆破
ffuf -u http://10.10.10.x/FUZZ \
    -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt

# 加副檔名
ffuf -u http://10.10.10.x/FUZZ \
    -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt \
    -e .php,.txt,.bak

# 過濾掉 404（某些應用 404 回傳 200）
ffuf -u http://10.10.10.x/FUZZ \
    -w wordlist.txt \
    -fc 404

# 根據大小過濾（大小相同的通常是同一個錯誤頁面）
ffuf -u http://10.10.10.x/FUZZ \
    -w wordlist.txt \
    -fs 1234
```

### dirb（老工具，備用）

```bash
dirb http://10.10.10.x /usr/share/wordlists/dirb/common.txt
```

### 字典選擇

```
SecLists 是 OSCP 備考最重要的字典集合：
/usr/share/seclists/Discovery/Web-Content/

常用字典：
  directory-list-2.3-medium.txt  → 目錄爆破（平衡速度和覆蓋）
  directory-list-2.3-big.txt     → 更全，更慢
  common.txt                     → 快速先試
  raft-medium-directories.txt    → 另一個選擇
```

如果 Kali 沒有 SecLists：
```bash
sudo apt install seclists
# 或
git clone https://github.com/danielmiessler/SecLists /usr/share/seclists
```

## 虛擬主機（vhost）枚舉

很多靶機用同一個 IP 跑多個虛擬主機（Virtual Host）。你直接連 IP 看到一個網站，但 `admin.target.htb` 可能是完全不同的應用。

### 設定 /etc/hosts

```bash
# 先把目標 hostname 加進去
echo "10.10.10.x target.htb" | sudo tee -a /etc/hosts

# 有 vhost 的情況下，再加子域名
echo "10.10.10.x admin.target.htb" | sudo tee -a /etc/hosts
```

### gobuster vhost 模式

```bash
gobuster vhost \
    -u http://target.htb \
    -w /usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt \
    --append-domain
```

### ffuf vhost 模式

```bash
ffuf -u http://10.10.10.x \
    -H "Host: FUZZ.target.htb" \
    -w /usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt \
    -fs 1234    # 過濾掉和預設頁面一樣大小的回應
```

## 技術指紋工具

### WhatWeb

```bash
whatweb http://10.10.10.x
# 輸出：WordPress 5.x, Apache 2.4.29, PHP 7.2, jQuery 3.x
```

### Wappalyzer（瀏覽器插件）

在 Firefox 安裝 Wappalyzer，打開靶機網站時自動辨識技術棧。

### nikto（自動化掃描）

```bash
nikto -h http://10.10.10.x
```

nikto 不是很精準，但會找：
- 預設憑證頁面
- 可能有問題的 HTTP 方法
- 常見的設定錯誤

輸出很多噪音，挑重要的看。

## CMS 特定枚舉

### WordPress

```bash
wpscan --url http://10.10.10.x

# 列出 plugin（找有漏洞的 plugin）
wpscan --url http://10.10.10.x --enumerate p

# 暴力破解用戶
wpscan --url http://10.10.10.x -U admin -P /usr/share/wordlists/rockyou.txt
```

### Drupal

```bash
# droopescan
droopescan scan drupal -u http://10.10.10.x
```

### Joomla

```bash
joomscan --url http://10.10.10.x
```

## 找備份和原始碼洩漏

很多開發者留下備份或設定：

```bash
# 常見備份檔名模式
ffuf -u http://10.10.10.x/FUZZ \
    -w /usr/share/seclists/Discovery/Web-Content/web-extensions.txt

# 直接試
curl http://10.10.10.x/backup.zip
curl http://10.10.10.x/config.php.bak
curl http://10.10.10.x/.git/HEAD     # 如果 git 目錄洩漏
curl http://10.10.10.x/.env          # 環境變數（常有密碼）
```

找到 `.git` 洩漏的話：

```bash
# git-dumper 工具
pip3 install git-dumper
git-dumper http://10.10.10.x/.git ./output
# 然後在 output/ 看原始碼
```

## 完整 Web 枚舉 Checklist

```
□ curl -I 看 HTTP header（伺服器版本、後端語言）
□ 瀏覽器手動瀏覽，看網站功能和結構
□ 看 robots.txt 和 sitemap.xml
□ WhatWeb 或 Wappalyzer 指紋
□ gobuster 目錄爆破（medium 字典 + 常見副檔名）
□ nikto 掃一遍
□ 如果有 hostname，試 vhost 枚舉
□ 如果是 CMS，用對應工具掃
□ 找備份檔和設定洩漏
□ 找到的每個路徑都手動訪問
```

## 本章對應靶機

| 機器 | Web 枚舉重點 |
|------|------------|
| HTB Beep | Elastix，CMS 版本漏洞 |
| HTB Jerry | Tomcat，預設憑證管理介面 |
| HTB Cronos | DNS 枚舉 + vhost + Web |
| THM Alfred | Jenkins，隱藏的管理路徑 |

## 自我檢核

- [ ] 能用 gobuster 跑目錄爆破，並加上 `.php`, `.bak` 副檔名
- [ ] 能設定 `/etc/hosts` 並用 gobuster vhost 模式找子域名
- [ ] 知道 ffuf 的 `-fs` 和 `-fc` 怎麼用（過濾噪音）
- [ ] 跑過 WhatWeb 並知道輸出代表什麼

→ [Ch 9 漏洞搜尋：searchsploit / Exploit-DB / CVE](./09-vuln-search.md)
