# Ch 4 — 網路基礎：TCP/IP、埠口、協定速查

> 目標：看到 nmap 結果或抓包輸出時能立刻理解，知道各個 port 代表什麼服務、攻擊面在哪。

## 為什麼滲透測試需要網路知識

你打的每一台機器，都要先搞清楚它「長什麼樣子」：開了哪些 port、跑什麼服務、用什麼版本。這些資訊是你的攻擊路徑起點。

看不懂 nmap 輸出 = 找不到入口。

## TCP vs UDP

```
TCP（傳輸控制協定）：
  建立連線：三次握手（SYN → SYN/ACK → ACK）
  可靠傳輸，有確認機制
  大多數服務用 TCP：HTTP、SSH、SMB、FTP

UDP：
  無連線，不可靠，快
  常見：DNS（53）、SNMP（161）、TFTP（69）
  nmap 預設不掃 UDP，要加 -sU 才掃
```

**為什麼重要**：你 nmap 掃完所有 TCP port，但如果目標的 SNMP（UDP 161）開著，你可能漏掉一個大情報來源。

## 常用 Port 速查表

這些 port 在 OSCP 靶機出現頻率最高：

| Port | 服務 | 常見攻擊面 |
|------|------|------------|
| 21   | FTP  | 匿名登入、目錄遍歷、暴力破解 |
| 22   | SSH  | 暴力破解（弱密碼）、舊版漏洞 |
| 23   | Telnet | 明文傳輸、暴力破解 |
| 25   | SMTP | 使用者枚舉（VRFY/EXPN） |
| 53   | DNS  | Zone Transfer、子域名枚舉 |
| 80/443 | HTTP/HTTPS | Web 應用漏洞（整個 Part 3） |
| 110  | POP3 | 暴力破解 |
| 139/445 | SMB | EternalBlue（MS17-010）、密碼 spray、Null session |
| 1433 | MSSQL | 預設憑證、xp_cmdshell |
| 3306 | MySQL | 預設憑證、UDF 提權 |
| 3389 | RDP  | 暴力破解、BlueKeep（CVE-2019-0708） |
| 5985/5986 | WinRM | 憑證得手後橫向移動 |
| 6379 | Redis | 未授權存取、RCE |
| 8080/8443 | HTTP alt | Web 管理介面，預設憑證 |

背這張表不是目的，**用的時候查就好**，但要知道「看到 445 就想到 SMB」這種直覺。

## IP 位址與子網路

OSCP 考試是一個隔離的內網環境，你需要知道：

```bash
# 你自己的 IP（連上 VPN 後）
ip addr show tun0

# 靶機通常在 10.10.10.0/24 或 10.129.x.x/16
# 掃整個子網路：
nmap -sn 10.10.10.0/24     # -sn = ping scan，只找活著的主機
```

子網路 CIDR 速查：

```
/24 → 254 台主機（10.10.10.1 ~ 10.10.10.254）
/16 → 65534 台主機
/8  → 16M 台主機（10.0.0.0/8 就是整個 10.x.x.x）
```

## DNS 基礎

DNS（Domain Name System）把域名解析成 IP。滲透測試中很重要：

```bash
# 查詢域名 IP
nslookup target.com
dig target.com

# 嘗試 Zone Transfer（舊設定可能允許）
dig axfr @nameserver target.com
# 成功的話你能拿到所有子域名清單

# 反向查詢（IP → 域名）
nslookup 10.10.10.x
dig -x 10.10.10.x
```

**為什麼 Zone Transfer 是漏洞**：如果 DNS 伺服器設定錯誤允許任意 Zone Transfer，你能一次拿到所有子域名，大幅增加攻擊面。

## HTTP 請求/回應基礎

Web 攻擊（Part 3）的基礎：

```
GET /login.php HTTP/1.1
Host: target.com
Cookie: session=abc123
User-Agent: Mozilla/5.0

→ 這是一個請求
```

```
HTTP/1.1 200 OK
Content-Type: text/html
Set-Cookie: session=newvalue; HttpOnly; Secure

<html>...</html>
→ 這是回應
```

重要 HTTP 狀態碼：

```
200 OK           → 正常
301/302 Redirect → 跳轉（有時洩漏路徑資訊）
400 Bad Request  → 請求格式錯
401 Unauthorized → 需要認證
403 Forbidden    → 有認證但沒權限（有時可繞過）
404 Not Found    → 找不到（但不代表路徑不存在）
500 Internal Error → 程式崩潰（可能有錯誤訊息洩漏）
```

## 抓包概念

你用 Burp Suite（Ch 10）時，它是一個 HTTP Proxy：

```
你的瀏覽器 → Burp（127.0.0.1:8080）→ 目標 Web Server
                ↑
            Burp 在這裡攔截、修改、重放請求
```

理解 Proxy 的原理，才能理解為什麼 Burp 能做到那麼多事。

## 常見協定的快速認識

### SMB（139/445）

Server Message Block，Windows 檔案共享協定。

```bash
# 列出 SMB 分享（不需要密碼的 Null session）
smbclient -L //10.10.10.x -N

# 連進分享
smbclient //10.10.10.x/share -N

# 枚舉使用者、分享、密碼政策
enum4linux -a 10.10.10.x
```

SMB 是 OSCP 高頻出現的服務，EternalBlue（MS17-010）打 SMBv1 是最著名的漏洞。

### SSH（22）

```bash
# 連線
ssh user@10.10.10.x

# 指定 key
ssh -i id_rsa user@10.10.10.x

# 用 SSH 做 port forwarding（Ch 38 Pivoting 會深入）
ssh -L 8080:127.0.0.1:80 user@10.10.10.x
```

### FTP（21）

```bash
ftp 10.10.10.x
# 嘗試匿名登入：username anonymous, password 任意 email

# 或用 curl
curl -v ftp://10.10.10.x --user anonymous:anonymous
```

## 自我檢核

- [ ] 看到 port 445 知道要想 SMB 攻擊面
- [ ] 能說出 TCP 三次握手的步驟
- [ ] 知道 Zone Transfer 是什麼，以及為什麼它是問題
- [ ] 能用 `smbclient -L` 列出分享
- [ ] 知道 `ss -tlnp` 能找本機監聽服務（上章複習）

→ [Ch 5 滲透測試方法論：枚舉 → 利用 → 提權 → 報告](./05-pentest-methodology.md)
