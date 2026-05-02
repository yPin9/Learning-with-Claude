# Ch 24 — 數位簽章與 PKI：X.509、CA chain、Let's Encrypt

> 目標：把 Ch 23 的「能簽訊息」延伸到「整套信任體系」 — X.509 證書格式、CA chain 怎麼建立信任、Let's Encrypt + ACME protocol、Certificate Transparency log、什麼是真正能信任的根 CA。

## 簽章 vs MAC vs Hash

```
Hash：    驗完整性，沒身份
MAC：     驗完整性 + 來源（對稱 key，雙方對等）
簽章：    驗完整性 + 來源 + 不可否認（公鑰，非對等）
```

簽章特別在**不可否認 (non-repudiation)**：

- Alice 用私鑰簽 → 只有 Alice 能簽
- Bob 用 Alice 公鑰驗 → 任何人能驗
- 第三方仲裁可信任「Alice 的確簽過」

但這個性質**只有當 Alice 私鑰沒洩漏**才成立。實務上「不可否認」也常被律法挑戰（key 是不是 Alice 自己保管？）。

## 簽章對應到 hash

直接簽長訊息成本高。**簽 hash**：

```
sign(m) = sign_priv(H(m))
verify(m, s) = verify_pub(H(m), s)
```

對 RSA：

```
s = pow(H(m), d, n)   (PKCS#1 v1.5 或 PSS padded)
verify: pow(s, e, n) == H(m) padded ?
```

對 ECDSA / EdDSA：直接 sign(H(m)) — Ed25519 內部還會再 hash 一次（含 R、A、m）。

**安全性歸到 hash 抗碰撞**：H 找到 collision → 簽章可偽造（同 hash 的訊息簽完互換）。**SHA-256 用於簽章夠安全**；MD5 / SHA-1 早被淘汰。

## X.509 證書

**X.509** 是 ITU 1988 訂的證書格式，現代 PKI 標準。一個證書包含：

```
Certificate (X.509 v3):
  Version: 3
  Serial Number: 隨機，CA 內唯一
  Signature Algorithm: e.g., sha256WithRSAEncryption
  Issuer: <CA 的 distinguished name>
  Validity:
    Not Before: 2024-01-01
    Not After:  2025-01-01
  Subject: <憑證主體 distinguished name>
  Subject Public Key Info:
    Algorithm: e.g., rsaEncryption
    Public Key: <key bytes>
  Extensions:
    Subject Alternative Name: DNS:example.com, DNS:www.example.com
    Key Usage: Digital Signature, Key Encipherment
    Extended Key Usage: Server Authentication
    Authority Key Identifier: <CA pub key hash>
    CRL Distribution Points: http://crl.example-ca.com/...
    Authority Information Access: http://ocsp.example-ca.com
  Signature: <CA 對上述全部的簽章>
```

**ASN.1 + DER encoding**。`openssl x509 -in cert.pem -text` 可以看人類版。

## CA Chain：信任傳遞

```
Root CA (self-signed)
   │ signs
   ▼
Intermediate CA
   │ signs
   ▼
Leaf certificate (server / user)
```

驗證一個 leaf：

1. 用 Intermediate 的公鑰驗 leaf 簽章
2. 用 Root 的公鑰驗 Intermediate 簽章
3. 確認 Root 在你的 trust store

**Trust store**：作業系統 / 瀏覽器內建一份「信任的 root CA」。Mozilla maintain 的 CA 列表 ~150 個 root，被 Linux distro 與多數 software 採用。

## 為什麼要 Intermediate CA？

```
Root CA：高度保護（離線、HSM、難取出 key）
Intermediate：日常簽 leaf 用

如果 Intermediate compromise：
  撤銷該 Intermediate（CRL / OCSP）
  Root 不受影響
  系統仍能信任新 Intermediate
```

實務上 root key **整年都離線**。每幾個月一次 ceremony 用來簽新的 Intermediate。Let's Encrypt 用 R3、E1 等 Intermediate，root 是 ISRG Root X1。

## Let's Encrypt：免費自動化 CA

2015 起 Internet Security Research Group (ISRG) 提供免費 SSL 證書。改變了 HTTPS 部署：

```
2014：HTTPS deployment ~30%
2024：~95%
```

關鍵：**ACME protocol**（自動化憑證申請）：

```
1. client 向 Let's Encrypt 註冊（生成 ACME account key）
2. client 申請 cert for example.com
3. CA 發 challenge（HTTP / DNS / TLS-ALPN）
4. client 在 server 上設好 challenge 回應
5. CA 驗證能反映客戶確實控制 example.com
6. CA 發證書
```

整個 < 5 分鐘 + 自動續簽。**完全免費**，且鼓勵 90 天短效期（短效期降低 key compromise 影響）。

```bash
# certbot 是 Let's Encrypt 官方 client
certbot certonly --webroot -w /var/www/example.com -d example.com
# 自動產 cert，存在 /etc/letsencrypt/live/example.com/
```

## Domain validation 三種方式

ACME 的 challenge：

- **HTTP-01**：在 `http://example.com/.well-known/acme-challenge/<token>` 放檔案
- **DNS-01**：在 `_acme-challenge.example.com` 加 TXT 記錄
- **TLS-ALPN-01**：在 TLS 握手時提供特殊 ALPN

DNS-01 唯一支援 wildcard（`*.example.com`）。實務 wildcard 證書必走 DNS。

## 證書類型

```
DV (Domain Validation):
  只驗「申請人控制此 domain」
  最便宜（Let's Encrypt 免費）
  瀏覽器 lock icon
  ~ 95% 使用情境

OV (Organization Validation):
  驗 domain + 組織存在
  幾百美元
  瀏覽器 lock icon（一樣）

EV (Extended Validation):
  深度驗證組織法律狀態
  上千美元
  早期瀏覽器顯示「綠色組織名」（已淘汰）
  現代沒實質 UX 差異
```

EV 與 OV **沒有提供額外加密**。差別只在「**人類驗證程度**」。多數場景 DV 夠用。

## 證書撤銷

當 leaf 私鑰外洩 / 主體解散 / 等：

### CRL (Certificate Revocation List)

CA 維護一份「已撤銷 cert serial number」的 list。client 下載對照。

問題：list 巨大（>MB）、不即時。

### OCSP (Online Certificate Status Protocol)

client 向 CA 即時 query：「這個 cert 還有效嗎？」

問題：每次 TLS handshake 多一次 round trip + privacy 洩漏（CA 看到誰連誰）。

### OCSP Stapling

server 預先向 CA 拿 OCSP response，handshake 時附在 cert 後。client 不用自己 query CA。**現代 TLS 預設**。

### Certificate Transparency (CT)

Google 2013 推。所有公開 trusted CA 簽發的 cert **必登 public log**。瀏覽器只接受有 CT proof 的 cert。

優點：CA 不能私下簽 rogue cert（被 CT log 抓包）。Google、Cloudflare 等 operate CT log。

```bash
# 查 example.com 的 CT log 紀錄
curl "https://crt.sh/?q=example.com&output=json"
```

## Code Signing

對軟體 binary 簽章。Microsoft Authenticode、Apple notarization、Linux package signing。

```
Authenticode (Windows):
  binary 含 PKCS#7 SignedData
  EV code signing cert（required for kernel driver）
  Windows SmartScreen 給「已知開發者」綠燈

Apple notarization:
  上傳 binary 給 Apple，他們掃 + 簽
  Gatekeeper 拒絕未 notarize 的 app
```

## Mozilla CA 政策

Mozilla 是 root CA 信任最透明的 maintainer。要進 Mozilla trust store：

- WebTrust / ETSI audit
- 公開 CP/CPS（Certificate Policy / Practice Statement）
- 嚴格 incident response
- 任何漏簽會被 distrust

歷史上被 distrust 過的 CA：

- **DigiNotar (2011)**：被駭，簽了 \*.google.com 假證書（Iran MITM）→ Mozilla / Microsoft / Apple 全部 distrust
- **Symantec (2017)**：流程混亂，多次違規 → Mozilla / Google 漸進 distrust
- **WoSign / StartCom (2016)**：誤簽 + 隱瞞 → Mozilla distrust

## Certificate Pinning

對最敏感 client（mobile app），不信任 trust store 的整個 CA list，**只信特定一兩個**：

```
sha256/abcdef...= ← 特定 cert 或 SubjectPublicKeyInfo 的 hash
```

如果 server cert hash 不對 → reject。即使 CA chain 合法。

抗 「某個 root CA 被駭發 rogue cert」的場景。但要小心：**pin 過期 / 換證書時不更新 app → 全用戶連線壞**。

## TLS 1.3 取消證書類型限制

TLS 1.2 cert 用途由 cipher suite 決定（RSA / DHE_RSA / ECDHE_RSA / ECDHE_ECDSA 等）。TLS 1.3 解耦：

- Server cert 只負責簽 transcript（用 RSA-PSS / ECDSA / EdDSA）
- Key exchange 獨立（DHE / ECDHE）

更乾淨。Ch 34 TLS 1.3 詳述。

## 一個常見誤解

「lock icon = 安全」

**lock icon 只代表「TLS 加密 + cert chain 驗證通過」**。不代表：

- 對方真的是你想連的網站（DV cert 只驗 domain，不驗組織意圖）
- 網站沒漏洞
- server 端不會洩漏資料

phishing 網站可以申請合法 Let's Encrypt cert，瀏覽器一樣顯示 lock。**TLS 解決傳輸層，不解決端點安全**。

## 自我檢核

- [ ] 我能解釋簽章與 MAC、hash 的差異
- [ ] 我能解析 X.509 cert 的關鍵欄位
- [ ] 我能畫出 CA chain 從 root 到 leaf
- [ ] 我能解釋 Let's Encrypt + ACME 的工作流程
- [ ] 我能說出 OCSP stapling、CT、cert pinning 各自解決什麼
- [ ] 我能列出至少一個 CA distrust 事件

到這裡 Part 5 公鑰密碼結束。下一個是練習 C — 手刻 RSA + 跑經典攻擊。

→ [練習 C：RSA + 攻擊](./practice-c-rsa-and-attacks.md)
