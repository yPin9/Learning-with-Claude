# Ch 24 — 數位簽章與 PKI

> 目標：理解簽章的安全定義（EUF-CMA），能解釋 X.509 certificate chain、CA 的角色、Let's Encrypt 的 ACME 流程。

---

## 為什麼需要這章

Ch 23 教了 ECDSA 和 EdDSA 的數學。但簽章要有用，需要回答一個根本問題：

> Alice 發了一個簽章，Bob 用一把公鑰驗證通過了。Bob 怎麼知道這把公鑰真的屬於 Alice？

如果攻擊者 Mallory 把自己的公鑰偽裝成 Alice 的，Mallory 就可以冒充 Alice 簽任何東西。

解法是 **Public Key Infrastructure（PKI）**：一套機制確保「公鑰 ↔ 身份」的綁定。X.509 certificate 和 Certificate Authority（CA）是現行 PKI 的核心。

---

## 先建立直覺

PKI 的比喻：

```
現實世界的身份驗證：
  護照 = 你的照片 + 你的名字 + 政府的蓋章
  「政府的蓋章」代表政府為「這張照片是這個人」做擔保

數位世界的身份驗證：
  X.509 證書 = 公鑰 + 域名/組織名 + CA 的數位簽章
  「CA 的簽章」代表 CA 為「這把公鑰屬於這個域名」做擔保
```

---

## 核心概念：數位簽章的安全定義

### EUF-CMA（Existential Unforgeability under Chosen Message Attack）

這是數位簽章的標準安全定義。直覺上：

```
遊戲（Security Game）：
  1. 挑戰者生成 key pair (sk, pk)，把 pk 給攻擊者
  2. 攻擊者可以要求挑戰者簽任意多個 message（Chosen Message Attack）
  3. 攻擊者要產生一個「新的」(message, signature) 對
     → 這個 message 不能是步驟 2 中已經簽過的

如果攻擊者在多項式時間內的成功機率是 negligible → 方案是 EUF-CMA 安全的

解讀：
  「Existential」：攻擊者只要能偽造任何一個新 message 的簽章就贏
  （比「Universal」弱——Universal 要求攻擊者能偽造指定 message 的簽章）
  「Chosen Message Attack」：攻擊者可以自由選擇要簽的 message
  （最強的攻擊模型）
```

### 簽章 vs MAC 的差異

| 面向 | MAC (HMAC) | 數位簽章 |
|---|---|---|
| Key 類型 | 對稱（雙方共享） | 非對稱（私鑰簽、公鑰驗） |
| 不可否認性 | 無（雙方都能產生 MAC） | 有（只有私鑰持有者能簽） |
| 驗證者 | 只有持有 key 的人 | 任何人 |
| 用途 | 訊息完整性 | 身份認證 + 不可否認性 |
| 效率 | 快 | 慢 |

**不可否認性（Non-repudiation）**：Alice 用私鑰簽了一份合約，事後不能說「不是我簽的」——因為只有她有私鑰。MAC 做不到這點（因為 Bob 也有同一把 key，可以自己造 MAC）。

---

## 底層機制：X.509 Certificate

### Certificate 的結構

```
X.509 v3 Certificate 結構：

┌──────────────────────────────────────┐
│  tbsCertificate (to-be-signed)       │
│  ┌────────────────────────────────┐  │
│  │ Version: v3                    │  │
│  │ Serial Number: 01:23:45:...   │  │
│  │ Signature Algorithm: sha256    │  │
│  │   WithRSAEncryption           │  │
│  │ Issuer: CN=Let's Encrypt R3   │  │
│  │ Validity:                      │  │
│  │   Not Before: 2024-01-01      │  │
│  │   Not After:  2024-04-01      │  │
│  │ Subject: CN=example.com       │  │
│  │ Subject Public Key:            │  │
│  │   Algorithm: ECDSA P-256      │  │
│  │   Key: 04:ab:cd:...           │  │
│  │ Extensions:                    │  │
│  │   SAN: example.com, *.ex...   │  │
│  │   Key Usage: Digital Sig...   │  │
│  │   Basic Constraints: CA:FALSE │  │
│  └────────────────────────────────┘  │
│                                      │
│  Signature Algorithm: sha256WithRSA  │
│  Signature Value: 3a:4b:5c:...      │
│  ← 這是 CA 用自己的私鑰對上面                │
│    tbsCertificate 做的簽章                    │
└──────────────────────────────────────┘
```

### 用 OpenSSL 查看 certificate

```bash
# 查看一個網站的 certificate
echo | openssl s_client -connect example.com:443 2>/dev/null | \
  openssl x509 -text -noout

# 關鍵欄位：
#   Issuer: 誰簽發的（CA）
#   Subject: 這個 cert 代表誰
#   Subject Alternative Name: 這個 cert 覆蓋的域名
#   Validity: 有效期
#   Public Key: 持有者的公鑰
```

### Certificate Chain（信任鏈）

```
Root CA（根憑證，自簽）
  │
  │ 用 Root 的私鑰簽發
  ↓
Intermediate CA（中間憑證）
  │
  │ 用 Intermediate 的私鑰簽發
  ↓
Leaf Certificate（終端憑證，給 example.com）

驗證流程：
  1. Browser 收到 leaf cert + intermediate cert
  2. 用 intermediate 的公鑰驗證 leaf cert 的簽章 ✓
  3. 用 root 的公鑰驗證 intermediate cert 的簽章 ✓
  4. Root cert 已預裝在 OS/browser 的 trust store ✓
  → 信任鏈完整，接受 leaf cert
```

為什麼不直接用 Root CA 簽 leaf cert？

```
原因 1：Root 私鑰太珍貴
  Root 私鑰被偷 = 整個 CA 信任體系崩潰
  Root 私鑰存在 offline HSM 中，幾年才拿出來用一次
  Intermediate 的私鑰在線上使用（自動化簽發）

原因 2：風險隔離
  如果 Intermediate 被打 → 只撤銷 Intermediate
  如果 Root 直接簽 → 打下一個 leaf 就影響整個 root

原因 3：可撤銷性
  Intermediate 可以被 root 撤銷
  Root 不能被撤銷（它是 trust anchor）
```

---

## 進一步用法：Let's Encrypt 與 ACME

### Let's Encrypt 解決的問題

2015 年以前：
- 從 CA 買一張 certificate：$50-$300/年
- 需要手動驗證域名所有權（email、DNS）
- 很多小網站因此不用 HTTPS

2015 年之後：
- Let's Encrypt 提供免費、自動化的 certificate
- ACME protocol（Automatic Certificate Management Environment）
- 90 天有效期 + 自動 renew
- 截至 2024 年：Let's Encrypt 簽發了超過 40 億張 certificate

### ACME 流程

```
Client (certbot)              Let's Encrypt (ACME Server)
    │ 1. 請求簽發 cert ──────────>│
    │<── 2. Challenge（證明你控制域名）│
    │ 3. 回應 challenge ─────────>│
    │    (HTTP-01: web token      │
    │     DNS-01: TXT record)     │
    │    4. LE 從多地點驗證 ─────── │
    │<── 5. 簽發 certificate ──── │
```

### Challenge 類型

| Challenge | 怎麼驗 | 優點 | 缺點 |
|---|---|---|---|
| HTTP-01 | 在 web server 放 token | 自動化容易 | 需要 port 80 開放 |
| DNS-01 | 在 DNS 放 TXT record | 支援 wildcard cert | 需要 DNS API access |
| TLS-ALPN-01 | 在 TLS handshake 中驗 | 不需要 port 80 | 較少使用 |

---

## Certificate Transparency（CT）

### 為什麼需要 CT

問題：CA 可能被入侵或脅迫，簽發偽造的 certificate。

```
歷史案例：
  2011: DigiNotar 被入侵 → 簽發了 *.google.com 的偽造 cert
  2013: 法國 ANSSI 的中間 CA 簽了 Google 域名的 cert
  2015: CNNIC 的中間 CA 被濫用
  2015: Symantec 簽發了不合規的 test cert
```

### CT 的機制

```
Certificate Transparency（RFC 6962）：

1. CA 簽發 cert 前，必須把 cert 提交到公開的 CT log server
2. CT log server 回傳一個 SCT（Signed Certificate Timestamp）
3. CA 把 SCT 嵌入 cert（或 server 在 TLS 中提供）
4. Browser 驗證 SCT 的存在

CT log 是 append-only 的 Merkle tree：
  - 任何人都可以監控 log
  - 無法秘密刪除已記錄的 cert
  - Google、Facebook 等公司有 CT monitor 持續掃描

效果：
  如果有人偽造了你的域名的 cert，CT log 會記錄
  你（或你的 monitor）可以發現並撤銷
```

Google Chrome 從 2018 年起要求所有新簽發的 cert 必須附帶 SCT（Certificate Transparency enforcement）。

---

## Certificate Revocation

### 為什麼需要撤銷

Certificate 有有效期，但在有效期內可能需要提前失效：
- 私鑰洩露
- CA 被入侵
- 域名轉讓

### CRL vs OCSP

| 機制 | 全稱 | 怎麼查 | 缺點 |
|---|---|---|---|
| CRL | Certificate Revocation List | 下載完整的撤銷列表 | 列表太大、更新慢 |
| OCSP | Online Certificate Status Protocol | 即時查詢一張 cert 的狀態 | 隱私問題（CA 知道你訪問誰） |
| OCSP Stapling | — | Server 主動附上 OCSP 回覆 | 需要 server 支援 |

### OCSP Stapling

```
沒有 Stapling：
  Client ──> Server（拿到 cert）
  Client ──> CA（查 OCSP，cert 被撤銷了嗎？）   ← 慢 + 隱私洩露
  CA ──> Client（回覆：有效/已撤銷）

有 Stapling：
  Server 定期跟 CA 要 OCSP 回覆（帶簽章的「有效」證明）
  Client ──> Server
  Server ──> Client（cert + OCSP staple）          ← 快 + 無隱私問題
  Client 驗證 staple 的 CA 簽章 ✓
```

### Chrome 的做法：CRLSets

Google Chrome 不用 CRL 也不用 OCSP（除了 EV cert）。Chrome 用自己的 **CRLSets**：一個精簡的撤銷列表，透過 Chrome 更新推送。只包含「高影響」的撤銷（例如 intermediate CA 被撤銷）。

---

## 對比與取捨

### 各種 PKI 模型

| 模型 | 信任基礎 | 優點 | 缺點 |
|---|---|---|---|
| CA-based (X.509) | 信任 root CA | 集中管理、自動化 | CA 是單點故障 |
| Web of Trust (PGP) | 互相簽署 | 去中心化 | 不可擴展、UX 差 |
| TOFU (SSH) | 首次連線時信任 | 零配置 | 首次連線時脆弱 |
| DANE (DNSSEC) | 信任 DNS | 不需要 CA | DNSSEC 部署率低 |
| CT + CA | CA + 公開監控 | 偽造會被發現 | 不防止偽造，只檢測 |

### 各簽章方案在 PKI 中的使用

| 用途 | 常用方案 | 趨勢 |
|---|---|---|
| Root CA 簽章 | RSA-4096 + SHA-256 | 穩定 |
| Intermediate CA 簽章 | RSA-2048 / ECDSA P-256 | 向 ECDSA 遷移 |
| Leaf cert 簽章 | ECDSA P-256 / Ed25519 | Ed25519 逐漸增加 |
| Code signing | RSA-2048 / ECDSA | 穩定 |
| DNSSEC | RSA-2048 / ECDSA P-256 | 向 ECDSA 遷移 |

---

## 踩雷集錦

### 雷 1：不檢查 certificate chain

```python
# 錯誤：跳過 cert 驗證
requests.get("https://example.com", verify=False)
# WARNING: 這關掉了所有 TLS 安全性！

# 正確：使用系統的 trust store
requests.get("https://example.com")  # verify=True 是預設值
```

### 雷 2：不做 hostname 驗證

TLS 庫可能驗證了 certificate chain（root → intermediate → leaf），但沒有檢查 leaf cert 的 Subject Alternative Name (SAN) 是否匹配你連線的域名。

```python
# 正確做法（大多數現代 library 自動做）：
# 1. 驗證 certificate chain
# 2. 驗證 leaf cert 的 SAN 包含目標域名
# 3. 驗證 cert 未過期
# 4. 檢查 revocation（OCSP staple 或 CRL）
```

### 雷 3：Certificate pinning 過頭

Certificate pinning（把特定 cert 或公鑰釘死在 client 中）可以防止 MITM，但如果 cert 過期或 CA 更換，client 就連不上了。Google 在 2017 年廢棄了 HTTP Public Key Pinning (HPKP) 標準——因為設定錯誤會讓網站完全無法訪問。

### 雷 4：忽略 Certificate Transparency

```bash
# 檢查你的域名是否有可疑的 cert
# crt.sh 查詢 CT log
curl "https://crt.sh/?q=example.com&output=json" | python -m json.tool
```

定期監控 CT log 可以發現有人偽造了你的 cert。

### 雷 5：以為 HTTPS 就代表「安全」

HTTPS（TLS + certificate）只保證：
1. 你跟 server 之間的通訊是加密的
2. server 持有某個 CA 簽發的 cert

不保證：
- server 本身沒有漏洞
- server 不是釣魚網站（Let's Encrypt 也會簽給 paypa1.com）
- 你的電腦沒有被植入惡意 root CA

---

## 進階

### Certificate Pinning 的演進

```
2011-2017: HPKP（HTTP Public Key Pinning）
  Server 用 HTTP header 告訴 browser「只接受這些公鑰的 cert」
  問題：設定錯誤 = 自鎖 → 網站無法訪問
  2017 年被 Chrome 廢棄

2017-now: Expect-CT → Certificate Transparency
  Server 用 HTTP header 要求 browser 檢查 CT log
  比 HPKP 安全：不會自鎖，只是告訴 browser「如果沒有 SCT 就拒絕」

2024-now: 現代做法
  用 CT 監控 + ACME 自動化
  不做 pinning（風險太大）
```

### Delegated Credentials（草案）

CDN 需要你的私鑰才能終止 TLS → 風險。Delegated Credentials 讓你簽一個 24 小時短期子憑證給 CDN，過期自動失效，私鑰暴露窗口從「cert 有效期」縮到 24 小時。

### Post-Quantum PKI

ML-DSA / SLH-DSA 的簽章和公鑰比 ECDSA 大 10-100 倍 → TLS handshake 封包變大。過渡方案：hybrid certificate（同時包含 ECDSA 和 ML-DSA 簽章）。

---

## 動手練習

1. **查看 certificate chain**：
   ```bash
   echo | openssl s_client -showcerts -connect google.com:443 2>/dev/null | \
     openssl x509 -text -noout
   ```
   找出 Issuer、Subject、SAN、有效期、簽章演算法。

2. **自簽 certificate**：
   ```bash
   # 生成私鑰
   openssl ecparam -genkey -name prime256v1 -out key.pem
   # 自簽 cert
   openssl req -new -x509 -key key.pem -out cert.pem -days 365 \
     -subj "/CN=localhost"
   # 查看
   openssl x509 -in cert.pem -text -noout
   ```

3. **CT log 查詢**：去 https://crt.sh/ 搜索你自己的域名（或任何域名），查看所有簽發過的 certificate。

4. **certbot 模擬**：用 certbot 的 `--dry-run` 模式模擬 ACME 流程：
   ```bash
   sudo certbot certonly --dry-run -d example.com --preferred-challenges http
   ```

5. **Python 驗證 certificate chain**：
   ```python
   from cryptography import x509
   from cryptography.hazmat.primitives import hashes
   # 讀取 cert，驗證 issuer 的簽章
   ```

---

## 重點整理

```
數位簽章安全定義（EUF-CMA）：
  攻擊者可以要求簽任意 message
  但無法偽造「沒簽過的」message 的簽章
  簽章 vs MAC：簽章有不可否認性

X.509 Certificate：
  binding = 公鑰 + 身份 + CA 簽章
  Certificate chain: root → intermediate → leaf
  Root cert 預裝在 OS/browser trust store

Let's Encrypt + ACME：
  免費、自動化的 certificate 簽發
  Challenge: HTTP-01（web token）/ DNS-01（TXT record）
  90 天有效期 + 自動 renew

Certificate Transparency（CT）：
  所有 cert 必須記錄在公開的 CT log
  Append-only Merkle tree
  任何人可以監控 → 偽造會被發現

Revocation：
  CRL（太大、太慢）
  OCSP（即時但有隱私問題）
  OCSP Stapling（server 附上 OCSP 回覆）

PKI 的脆弱性：
  CA 被入侵 → DigiNotar (2011)
  CA 被濫用 → CNNIC (2015)
  防禦：CT 監控 + 多方驗證
```

---

## 自我檢核

- [ ] 我能解釋 EUF-CMA 的含義
- [ ] 我能畫出 X.509 certificate chain（root → intermediate → leaf）
- [ ] 我能解釋 ACME 的 challenge-response 流程
- [ ] 我知道 Certificate Transparency 解決什麼問題
- [ ] 我能區分 CRL / OCSP / OCSP Stapling
- [ ] 我能解釋為什麼 Root CA 不直接簽 leaf cert
- [ ] 我能解釋簽章和 MAC 在不可否認性上的差異

---

## 延伸閱讀

- **RFC 5280**：X.509 PKI 的核心標準
- **RFC 8555**：ACME 協議
- **RFC 6962**：Certificate Transparency
- **Let's Encrypt How It Works**：https://letsencrypt.org/how-it-works/
- **"Killing the Password: Certificate-Based Authentication"**（Cloudflare blog）
- **CT 監控工具**：https://crt.sh/，https://transparencyreport.google.com/https/certificates

---

## 下一章連結

[Ch 25 — AEAD 概念](./25-aead-concept.md)：Part 5（公鑰密碼）到此結束。接下來進入 Part 6——把加密和認證組合在一起的 AEAD（Authenticated Encryption with Associated Data），看 AES-GCM 和 ChaCha20-Poly1305 如何在一步中完成機密性和完整性。
