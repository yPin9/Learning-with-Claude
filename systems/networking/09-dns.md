# Ch 9 — DNS

> 目標：搞懂 DNS 怎麼把 domain 變成 IP、recursive 跟 authoritative 的差別、各種 record type、現代 DoH/DoT。

## DNS 的工作

「把 `example.com` 翻成 `93.184.216.34`」。

但「翻譯」這件事比想像複雜：
- 全球億萬個 domain
- 沒有單一 server 知道全部
- 要快、要可靠、要能 cache

DNS 的解：**分層 + 遞迴**。

## DNS 階層

```
                    ┌───────────────────┐
                    │ Root (.)          │
                    │ 13 個全球 server   │
                    └─────────┬──────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐    ┌──────────┐    ┌──────────┐
        │ .com     │    │ .org     │    │ .tw      │  ← TLD (Top-Level Domain)
        └─────┬────┘    └──────────┘    └──────────┘
              │
        ┌─────┴────┐
        ▼          ▼
  ┌──────────┐  ┌──────────┐
  │example.com│  │google.com│  ← Authoritative DNS
  └─────┬─────┘  └──────────┘
        │
        ▼
  www.example.com → 93.184.216.34
  api.example.com → 93.184.216.50
```

每一層只知道下一層的 server。

## 一次完整 DNS 查詢

你查 `www.example.com`：

```
 你 → 你的 DNS resolver（如 8.8.8.8）：「www.example.com 的 A record」
                │
                ├─→ Root server：「我不知道，問 .com」
                │
                ├─→ .com server：「我不知道，問 example.com 的 NS」
                │
                ├─→ example.com NS：「www.example.com → 93.184.216.34」
                │
 你 ← resolver：「是 93.184.216.34」
```

resolver 做「**遞迴查詢**」幫你跑 4 步。

## Authoritative vs Recursive

| 類型 | 角色 |
|---|---|
| **Authoritative** | 「我**擁有** example.com 的紀錄」 |
| **Recursive (resolver)** | 「我幫你問所有 server，找出答案」 |

你的 OS / browser 不會自己跑 4 步遞迴。設定的 DNS server（家用路由器、Google 8.8.8.8、Cloudflare 1.1.1.1）是 recursive resolver，幫你跑。

Authoritative server 不幫你查別的 domain。它只回**自己負責的 domain**。

## DNS Record types

常見：

| Type | 用途 | 例 |
|---|---|---|
| **A** | IPv4 位址 | example.com → 93.184.216.34 |
| **AAAA** | IPv6 位址 | example.com → 2606:2800:220:1:248:1893:25c8:1946 |
| **CNAME** | 別名 | www → example.com |
| **MX** | 郵件伺服器 | example.com → mail.example.com priority 10 |
| **TXT** | 任意文字 | SPF / DKIM / domain ownership 驗證 |
| **NS** | 該 domain 的 authoritative nameserver | example.com → ns1.example.com |
| **SOA** | Start of Authority | zone 元資料 |
| **PTR** | reverse lookup（IP→domain） | 34.216.184.93.in-addr.arpa → example.com |
| **SRV** | service record | _sip._tcp.example.com → 5060 |
| **CAA** | 哪些 CA 能簽憑證 | example.com → "letsencrypt.org" |

## 用 dig 查 DNS

```bash
# 基本查 A record
dig example.com

# 指定 record type
dig example.com MX
dig example.com NS
dig example.com TXT

# 指定 DNS server
dig @1.1.1.1 example.com

# 短輸出
dig +short example.com

# 看完整查詢過程（trace）
dig +trace example.com

# reverse lookup
dig -x 8.8.8.8
```

`dig +trace` 是學 DNS 階層最棒的工具：

```
 ;; 從 root 開始
 .                       . (root) NS record
 com.                    .com NS record
 example.com.            example.com NS record
 example.com.            example.com A record (final answer)
```

## DNS 快取

每層都 cache：

```
 你的 OS → resolver → root → TLD → authoritative
   ↑          ↑         ↑       ↑         ↑
  快取       快取      快取    快取      源
```

每個 record 有 **TTL**（time-to-live），到期前不重查。

```bash
dig example.com
# ;; ANSWER SECTION:
# example.com.    7200    IN    A    93.184.216.34
#                  ^
#                  TTL = 7200 秒 = 2 小時
```

TTL 設定影響：

- **TTL 短**（60 秒）：DNS 改動快生效，但 query 多
- **TTL 長**（86400 秒 = 1 天）：DNS query 少，但改動慢生效

production 通常 1-6 小時。**改 IP 前 1 天先改短 TTL**，等 propagate 完再改 IP。

## DoH / DoT（DNS over HTTPS / TLS）

傳統 DNS 走 UDP 53，**明文**。問題：

- ISP / 防火牆 / 政府能看你查什麼
- 能 spoof 假 reply（hijack）
- 中間機構能 censor

解決：

- **DoT** (DNS over TLS, port 853)：DNS 包在 TLS 裡
- **DoH** (DNS over HTTPS, port 443)：DNS 包在 HTTPS 裡，看起來像普通 web

支援的 resolver：Cloudflare 1.1.1.1, Google 8.8.8.8, Quad9 9.9.9.9。

```bash
# DoT
kdig -d @1.1.1.1 +tls example.com

# Firefox / Chrome 內建 DoH，settings 開
```

## 一個常見誤解：「DNS 一定走 UDP」

**錯**。DNS 預設 UDP 53，但：

- response 太大（> 512 byte）→ fall back TCP
- DNSSEC 簽章常超過 → 用 TCP 多
- zone transfer → 一定 TCP
- DoH/DoT → HTTPS/TLS 走 TCP

「**只用 UDP**」的 DNS 防火牆設定**會壞事**。

## 一個常見誤解：「8.8.8.8 是 Google 的 DNS server，是 authoritative」

**錯**。8.8.8.8 是 **recursive resolver**，不是 google.com 的 authoritative。

google.com 的 authoritative 是 `ns1.google.com` 等。

## 一個常見誤解：「改 hosts 檔比改 DNS 快」

**部分對**。`/etc/hosts` 在所有 DNS 之前查，立刻生效。但**只影響你的機器**。

production 改 IP → 改 DNS（影響全球）。

## 一個常見誤解：「DNS 改了立刻生效」

**錯**。DNS cache 在你電腦 / 路由器 / ISP / 各層 resolver。

「**全球 propagate 完**」常常要 24-48 小時。Production 改 DNS 要計畫。

## 動手練習

**1. dig 各種 record type**

```bash
dig example.com         # A
dig example.com AAAA    # IPv6
dig example.com MX      # mail
dig example.com NS      # nameserver
dig example.com TXT     # text records
dig +trace example.com  # 完整階層
dig -x 8.8.8.8         # reverse
```

**2. 看 cache**

```bash
# 第一次查（沒 cache）
dig example.com

# 立刻查第二次（有 cache，TTL 倒數）
dig example.com
```

對比 query time。

**3. 改 DNS server**

```bash
# 暫時用 Cloudflare DNS
dig @1.1.1.1 example.com

# 永久（看你的 OS）
sudo vi /etc/resolv.conf
# nameserver 1.1.1.1
```

**4. 故意設錯**

```bash
# /etc/hosts 加一行
echo "1.2.3.4 example.com" | sudo tee -a /etc/hosts

# 看
curl -v https://example.com    # 連 1.2.3.4
ping example.com               # 也是 1.2.3.4

# 移除（記得改回來）
sudo vi /etc/hosts
```

**5. tcpdump DNS**

```bash
sudo tcpdump -nn -i any 'port 53' &
dig example.com
```

看 query + response 的 packet。

## 自我檢核

- [ ] 講得出 DNS 階層（root → TLD → authoritative）
- [ ] recursive vs authoritative 差別
- [ ] 7+ 種 record type 用途記得
- [ ] 用 dig +trace 看過完整查詢
- [ ] 知道 DoH/DoT 解決什麼問題
- [ ] 知道 TTL 對 propagation 影響

下一章看 HTTP 演進 — 從 1.1 到 2 到 3。

→ [Ch 10 HTTP/1.1 → HTTP/2 → HTTP/3](./10-http-evolution.md)
