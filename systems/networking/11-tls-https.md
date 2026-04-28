# Ch 11 — TLS / HTTPS

> 目標：搞懂 TLS 怎麼做加密 + 驗證、憑證鏈如何運作、TLS 1.2 vs 1.3 差別。

## TLS 是什麼

**Transport Layer Security** — 在 TCP 上加：

1. **加密**：別人聽不到內容
2. **驗證**：確認對方真的是它聲稱的（防 MITM）
3. **完整性**：內容沒被改

歷史：SSL 1.0 → 2.0 → 3.0 → TLS 1.0 → 1.1 → 1.2 → 1.3。

**SSL 已死**，TLS 1.0 / 1.1 也已被 deprecate。**現代只用 TLS 1.2 / 1.3**。

## HTTPS = HTTP over TLS

```
 application: HTTP
              ↓
       TLS (encryption)
              ↓
       TCP (transport)
              ↓
       IP (routing)
```

HTTPS 預設 port 443（HTTP 是 80）。

## TLS 1.2 握手（最經典）

```
 client                              server
   │                                   │
   ├── ClientHello ────────────────────►│
   │   - TLS 版本                        │
   │   - 支援的 cipher suites            │
   │   - 隨機數 R1                       │
   │   - SNI: example.com                │
   │                                   │
   │◄── ServerHello, Certificate, ──────┤
   │    ServerKeyExchange,                │
   │    ServerHelloDone                   │
   │   - 選的 cipher suite               │
   │   - 隨機數 R2                       │
   │   - server 憑證                     │
   │   - server 公鑰                     │
   │                                   │
   │ (client 驗證憑證)                   │
   │                                   │
   ├── ClientKeyExchange ──────────────►│
   │   - 用 server 公鑰加密的 pre-master  │
   │                                   │
   │ (雙方各自計算 master secret)        │
   │                                   │
   ├── ChangeCipherSpec, Finished ─────►│
   │                                   │
   │◄── ChangeCipherSpec, Finished ─────┤
   │                                   │
   │  = 加密管道建立，2 RTT =           │
```

## TLS 1.3 握手（簡化 + 更快）

主要變化：

- **1 RTT**（vs 1.2 的 2 RTT）
- 0-RTT（resumption 重連時）
- 只支援 PFS cipher suites
- 移除舊算法（RC4, SHA-1, MD5, ...）

```
 client                              server
   │                                   │
   ├── ClientHello + KeyShare ─────────►│
   │   (含 client 的 keyshare)          │
   │                                   │
   │◄── ServerHello + KeyShare,─────────┤
   │    Certificate, EncryptedExt,      │
   │    Finished                        │
   │                                   │
   ├── Finished ──────────────────────►│
   │                                   │
   │  = 1 RTT 完成 =                   │
```

**TLS 1.3 普及**：browsers 預設用 1.3，server 都該支援。

## 憑證（Certificate）

server 的「**身份證**」：

```
Certificate:
    Data:
        Version: 3 (0x2)
        Serial Number: 04:00:00:00:00:01:1d:90:9f:b1:e1
        Signature Algorithm: sha256WithRSAEncryption
        Issuer: C=US, O=Let's Encrypt, CN=R3
        Validity:
            Not Before: 2025-01-01
            Not After:  2025-04-01
        Subject: CN=example.com
        Subject Public Key Info:
            Public Key Algorithm: rsaEncryption
            Public-Key: (2048 bit)
            ...
        X509v3 Subject Alternative Name:
            DNS: example.com, DNS: www.example.com, DNS: api.example.com
    Signature Algorithm: sha256WithRSAEncryption
    Signature: <由 issuer 簽>
```

關鍵欄位：

- **Issuer**：誰簽的（CA）
- **Subject**：給誰用的（domain）
- **Validity**：有效期
- **Public Key**：公鑰
- **SAN**（Subject Alternative Name）：其他適用的 domain
- **Signature**：CA 用自己私鑰簽

## 憑證鏈（Certificate Chain）

server 憑證 → 簽它的中間 CA → 簽 CA 的 root CA：

```
 ┌────────────────────────────┐
 │ server cert (example.com)  │  ← 憑證
 │ Issuer: Let's Encrypt R3   │
 └────────────┬───────────────┘
              │ R3 簽
              ▼
 ┌────────────────────────────┐
 │ Intermediate (R3)          │
 │ Issuer: ISRG Root X1       │
 └────────────┬───────────────┘
              │ Root X1 簽
              ▼
 ┌────────────────────────────┐
 │ Root CA (ISRG Root X1)     │  ← 內建在 OS / 瀏覽器
 │ Self-signed                │
 └────────────────────────────┘
```

驗證：你信任 root → root 簽中間 → 中間簽 server → 你信任 server。

**root CA 預先安裝**在你的 OS / 瀏覽器（幾百個）。

## 常見 CA

| CA | 特點 |
|---|---|
| **Let's Encrypt** | 免費、自動化、最廣用 |
| DigiCert | 商業，企業多 |
| GlobalSign | 商業 |
| Sectigo (前 Comodo) | 商業 |
| Google Trust Services | Google 自家 |

**Let's Encrypt 改變了世界** — 之前 SSL cert 一年幾千塊，現在免費。**現代 web 90% HTTPS** 因為它。

## TLS 1.3 cipher suite

cipher suite 規定用什麼算法：

```
 TLS_AES_256_GCM_SHA384
 │   │      │   │
 │   │      │   └── HMAC（完整性）
 │   │      └────── mode（GCM = Galois/Counter Mode）
 │   └─────────── 對稱加密（AES-256）
 └───────────── TLS 版本
```

TLS 1.3 cipher suite 只剩 5 個（vs 1.2 的數十個）。**簡化 = 少 bug**。

## SNI（Server Name Indication）

問題：1 個 IP 跑多個 HTTPS domain，server 怎麼知道要回哪個 domain 的憑證？

解：client 在 ClientHello 中**明文**告訴 server 「我要連 example.com」（SNI extension）。

副作用：**ISP / 防火牆能看到你連哪個 domain**（雖然內容加密）。

新解：**Encrypted ClientHello (ECH)** — 連 SNI 都加密。2024 年起逐漸部署。

## HSTS

「**這個 domain 永遠用 HTTPS**」 — server 回 header：

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

瀏覽器之後**一年內**對這 domain 的 `http://` request 自動轉 `https://`。

防止 SSL stripping 攻擊。

## 一個常見誤解：「HTTPS 防一切」

**部分對**。HTTPS 提供：

- 內容加密
- 對方身份驗證
- 完整性

**HTTPS 不防**：

- DNS 查詢（你查什麼 domain 別人看得到，除非 DoH）
- IP / 連線時間（traffic analysis）
- SNI（看你連哪 domain，除非 ECH）
- server 端的問題（server 被 hack 還是死）

## 一個常見誤解：「自簽 cert 不安全」

**部分對**。自簽 cert 加密強度跟 CA 簽的一樣。問題在**驗證**：

- CA 簽的 → 瀏覽器自動信
- 自簽 → 瀏覽器警告，要手動加例外

公網 server 用 CA 簽的（免費 Let's Encrypt）。內網 / 開發環境用自簽 OK。

## 一個常見誤解：「TLS 1.3 完全相容 1.2」

**部分對**。TLS 1.3 client 跟 1.2 server 能 fallback。但**老 client（IE 11、舊 Android）不支援 1.3**。

server 通常開 1.2 + 1.3 雙支援。

## 一個常見誤解：「憑證越貴越安全」

**錯**。TLS 安全強度跟價錢無關。商業 CA 賣的「**EV（Extended Validation）**」憑證，瀏覽器以前顯示綠色公司名 — 但 2019 後瀏覽器移除了這 UI。

EV 沒安全優勢，只是 marketing。**Let's Encrypt 的免費 cert 跟 1000 USD 的 EV 一樣安全**。

## 動手練習

**1. 看憑證**

```bash
openssl s_client -connect example.com:443 -servername example.com < /dev/null 2>/dev/null | openssl x509 -noout -text | head -30
```

**2. 看憑證鏈**

```bash
openssl s_client -connect example.com:443 -servername example.com -showcerts < /dev/null 2>/dev/null | grep "subject\|issuer"
```

**3. 看 cipher**

```bash
openssl s_client -connect example.com:443 -servername example.com < /dev/null 2>/dev/null | grep -E "Cipher|Protocol"
```

**4. 故意連過期 cert**

```bash
curl https://expired.badssl.com
# error
curl -k https://expired.badssl.com    # -k ignore cert
```

**5. tcpdump 看 TLS 握手**

```bash
sudo tcpdump -nn -i any 'host example.com and port 443' &
curl https://example.com
```

看 ClientHello / ServerHello packet。

**6. 測 server 支援的 TLS 版本**

```bash
nmap --script ssl-enum-ciphers -p 443 example.com
```

## 自我檢核

- [ ] 講得出 TLS 1.2 vs 1.3 的握手差異
- [ ] 憑證鏈三層（server / intermediate / root）清楚
- [ ] 知道 Let's Encrypt 為什麼改變了世界
- [ ] SNI 是什麼、有什麼副作用
- [ ] HSTS 防什麼
- [ ] 用 openssl 看過憑證

下一章看 SSH 與其他應用層協定速覽。

→ [Ch 12 SSH 與其他應用層速覽](./12-ssh-and-others.md)
