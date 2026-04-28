# Ch 15 — dig / nslookup

> 目標：精通 DNS debug 工具，能診斷各種 DNS 問題。

## dig 為什麼是 DNS debug 首選

`dig` (Domain Information Groper) 比 `nslookup` 強：

- 輸出更詳細
- 顯示 DNS server 的真實回應
- 支援 +trace（看完整階層）
- 支援所有 record type
- 適合 script 解析

`nslookup` 較舊、輸出簡略，**只在 dig 沒裝時用**。

## dig 基本

```bash
dig example.com           # 查 A record（預設）
dig example.com MX        # MX record
dig example.com NS        # nameserver
dig example.com TXT       # TXT
dig example.com AAAA      # IPv6
dig example.com ANY       # 所有 record type（很多 server 不再支援 ANY）
```

## dig 輸出解讀

```
$ dig example.com

; <<>> DiG 9.18.18 <<>> example.com
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 12345
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags:; udp: 1232

;; QUESTION SECTION:
;example.com.            IN    A

;; ANSWER SECTION:
example.com.    7200    IN    A    93.184.216.34

;; Query time: 12 msec
;; SERVER: 1.1.1.1#53(1.1.1.1) (UDP)
;; WHEN: Mon Apr 28 12:00:00 UTC 2025
;; MSG SIZE  rcvd: 56
```

讀法：

- `status: NOERROR` — 查詢成功
  - 其他可能：NXDOMAIN（不存在）, SERVFAIL（server 出錯）, REFUSED
- `flags: qr rd ra`
  - `qr` — 這是 response
  - `rd` — recursion desired
  - `ra` — recursion available
- `ANSWER SECTION` — 答案
  - `7200` — TTL
  - `IN` — class（Internet）
  - `A` — record type
  - `93.184.216.34` — 答案
- `Query time` — 多久
- `SERVER` — 用哪個 DNS server

## 各種 dig flag

```bash
# 短輸出（只答案）
dig +short example.com

# 不要 cache，從 root 開始查
dig +trace example.com

# 強制 TCP
dig +tcp example.com

# 指定 DNS server
dig @8.8.8.8 example.com
dig @1.1.1.1 example.com
dig @ns1.example.com example.com

# 多 query
dig example.com mx ns aaaa

# reverse lookup
dig -x 8.8.8.8

# 不要 recursion（直接問 authoritative）
dig +norecurse @ns1.example.com example.com

# DNSSEC
dig +dnssec example.com

# DNS over TLS
dig +tls @1.1.1.1 example.com

# DNS over HTTPS（要 kdig）
kdig -d @1.1.1.1 +https example.com
```

## dig +trace

最強 debug 工具。從 root 開始一步步查：

```bash
dig +trace example.com
```

輸出：

```
.                       86400    IN  NS      a.root-servers.net.
                        86400    IN  NS      b.root-servers.net.
                        ...
;; Received 239 bytes from 1.1.1.1 in 12 ms

com.                    172800   IN  NS      a.gtld-servers.net.
                        172800   IN  NS      b.gtld-servers.net.
                        ...
;; Received 1216 bytes from a.root-servers.net (198.41.0.4) in 25 ms

example.com.            172800   IN  NS      a.iana-servers.net.
example.com.            172800   IN  NS      b.iana-servers.net.
;; Received 92 bytes from a.gtld-servers.net (192.5.6.30) in 30 ms

example.com.            86400    IN  A       93.184.216.34
;; Received 56 bytes from a.iana-servers.net (199.43.135.53) in 40 ms
```

清楚看到「**root → .com → example.com**」每階段。

debug DNS 第一招就是 `dig +trace`，看哪階段壞。

## 一些常見 debug 場景

### 1. domain 解不到

```bash
$ dig example.com
;; status: NXDOMAIN
```

意思 domain 不存在。check：

- 拼錯？
- registry 過期？

```bash
whois example.com   # 看 registration 狀態
```

### 2. 解到舊 IP

```bash
$ dig example.com
;; ANSWER SECTION:
example.com. 86400 IN A 1.1.1.1   # 但你已經改 server IP 了
```

cache 沒 expired。check：

```bash
# 強制不用 cache
dig +trace example.com

# 或問 authoritative server
dig @ns1.example.com example.com
```

如果 authoritative 回新 IP → 等 cache expire（看 TTL）。

### 3. 不同 server 回不同答案

```bash
dig @8.8.8.8 example.com    # 1.1.1.1
dig @1.1.1.1 example.com    # 2.2.2.2
```

可能：

- DNS 改了，部份 server cache 還沒更新
- DNS poisoning / hijacking
- geo-DNS（按地理區回不同 IP，正常）

### 4. SERVFAIL

```bash
$ dig example.com
;; status: SERVFAIL
```

DNS server 內部錯誤。可能：

- DNSSEC 驗證失敗（記錄被改 / 簽章過期）
- recursive resolver 問不到 authoritative
- network 問題

```bash
# 換 DNS server 試
dig @1.1.1.1 example.com
```

### 5. 查 mail server

```bash
dig example.com MX
# 10  mail.example.com.
# 20  backup-mail.example.com.
```

`10` / `20` 是 priority（小者優先）。

寄信前的 sender 會：

1. 查 dst 的 MX
2. 連 priority 最小的 mail server
3. 失敗 → 試 priority 次小的

## nslookup 簡介

```bash
# 互動模式
nslookup
> example.com
> set type=mx
> example.com
> server 8.8.8.8
> example.com
> exit
```

或非互動：

```bash
nslookup example.com
nslookup example.com 8.8.8.8
nslookup -type=mx example.com
```

**輸出比 dig 簡略**，少資訊。**dig 在的話用 dig**。

## host 命令

更簡略的 DNS 工具：

```bash
host example.com
# example.com has address 93.184.216.34
# example.com has IPv6 address 2606:2800:220:1:248:1893:25c8:1946
# example.com mail is handled by 0 .

host -t mx example.com
host -a example.com    # 全部 record
```

寫 script 用 host 比較簡單（output 簡單）。

## /etc/resolv.conf

Linux 預設 DNS server 設定：

```
# /etc/resolv.conf
nameserver 1.1.1.1
nameserver 8.8.8.8
search example.com
```

`nameserver` — DNS server 順序  
`search` — short hostname 自動加這個 domain

但**多數現代 distro 用 systemd-resolved 管理**，`/etc/resolv.conf` 會被覆蓋：

```bash
# 看 systemd-resolved 設定
resolvectl status
resolvectl dns                   # 當前 DNS server
resolvectl flush-caches          # 清 cache
```

## /etc/hosts

DNS 之前的「**手動 mapping**」：

```
# /etc/hosts
127.0.0.1   localhost
192.168.1.10  myserver
```

優先於 DNS。debug 用 / 開發環境 mock domain 用。

```bash
# 暫時讓某 domain 解到本機 server
echo "127.0.0.1   example.com" | sudo tee -a /etc/hosts
curl https://example.com   # 連 127.0.0.1
```

**改完別忘了改回來**。

## 一個常見誤解：「dig 跟 ping 都查 DNS，差不多」

**不**。

- `ping` 是「**測網路 + DNS**」，輸出 ping 結果
- `dig` 是「**只測 DNS**」，輸出 DNS 細節

debug DNS 用 dig，不要用 ping（看不到細節）。

## 一個常見誤解：「DNS cache 是 OS 的事」

**部分對**。多層 cache：

- 應用程式（瀏覽器、curl 不太 cache）
- OS（systemd-resolved / nscd / dnsmasq）
- 路由器
- ISP DNS

每層 TTL 獨立倒數。要全清要逐層處理。

## 一個常見誤解：「`dig` 就反映瀏覽器看到的」

**部分對**。瀏覽器有自己的 DNS cache（Chrome 60 秒）。

```
chrome://net-internals/#dns      # Chrome
about:networking#dns             # Firefox
```

清瀏覽器 cache：通常重啟。

## 動手練習

**1. dig 各種 record**

```bash
dig google.com
dig google.com mx
dig google.com ns
dig google.com txt
dig google.com aaaa
```

**2. dig +trace**

```bash
dig +trace example.com
```

數有幾層、每層問哪個 server。

**3. 對比不同 DNS server**

```bash
dig @8.8.8.8 example.com
dig @1.1.1.1 example.com
dig @9.9.9.9 example.com
```

response time 對比。

**4. 看自己的 DNS 設定**

```bash
cat /etc/resolv.conf
resolvectl status   # systemd-resolved
```

**5. 故意改 hosts**

```bash
# 加一行
echo "1.2.3.4   example.com" | sudo tee -a /etc/hosts

# dig 看（dig 跳過 /etc/hosts，所以還是顯示真 IP）
dig example.com

# 但 ping / curl 走 hosts
ping -c 1 example.com    # 1.2.3.4
curl -I http://example.com   # 連 1.2.3.4

# 移除
sudo vi /etc/hosts
```

dig 不看 /etc/hosts，這是它跟 ping/curl 行為不一樣的點。

## 自我檢核

- [ ] dig 輸出讀得懂（status / flags / ANSWER）
- [ ] 知道 NXDOMAIN / SERVFAIL / NOERROR 各意義
- [ ] dig +trace 用得順
- [ ] 知道 dig vs nslookup vs host 各用途
- [ ] 知道 /etc/resolv.conf vs systemd-resolved 關係
- [ ] /etc/hosts 用過 mock domain

下一章看路徑診斷工具：traceroute / mtr / ping。

→ [Ch 16 traceroute / mtr / ping](./16-traceroute-mtr-ping.md)
