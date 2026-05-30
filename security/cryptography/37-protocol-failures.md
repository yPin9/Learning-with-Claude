# Ch 37 — Protocol 出錯精選：四個改變產業的漏洞

> 目標：從四個真實 protocol failure（Heartbleed、ROBOT、Logjam、TLS Triple Handshake）理解 protocol-level 漏洞的破壞力——密碼學原語設計得再好，protocol 或 implementation 出錯就全盤皆輸。

---

## 為什麼需要研究 Protocol Failures

前三章講了 TLS 1.3、Signal Protocol、Noise Framework 的「正確設計」。你可能會覺得：「只要用最新的 protocol 就安全了。」

不。

安全性有三層：

| 層級 | 例子 | 出錯的後果 |
|------|------|-----------|
| **密碼學原語** | AES、SHA-256、X25519 | 所有依賴該原語的 protocol 都被破（極少發生） |
| **Protocol 設計** | TLS 1.2 允許 RSA key transport | ROBOT、Logjam、Triple Handshake |
| **Implementation** | OpenSSL 的程式碼 | Heartbleed |

本章的四個案例橫跨第二層和第三層。它們的共同教訓：**攻擊者不需要破解 AES 或找到 SHA-256 碰撞——找到 protocol 邏輯漏洞或 implementation bug 就夠了。**

---

## 先建立直覺

四個漏洞的一句話摘要：

| 漏洞 | 年份 | 一句話 |
|------|------|--------|
| Heartbleed | 2014 | OpenSSL 忘了 check length → 讀到 server 記憶體裡的 private key |
| ROBOT | 2017 | RSA PKCS#1 v1.5 的 error message 差異 → padding oracle 回歸 |
| Logjam | 2015 | 512-bit DH 可以被 precompute → MITM downgrade |
| Triple Handshake | 2014 | TLS 1.2 的 handshake 沒有 transcript binding → client cert relay |

接下來逐一拆解。

---

## 案例一：Heartbleed（CVE-2014-0160）

### 漏洞原理

Heartbleed 不是密碼學錯誤——是 OpenSSL 的一個 **C 程式碼 bug**。

TLS 有一個 extension 叫 Heartbeat（RFC 6520）：client 送一個「你好」的 payload，server 原封不動送回來——用途是保持連線活躍（keep-alive）。

Heartbeat request 格式：

```
struct {
    HeartbeatMessageType type;   // 1 byte
    uint16 payload_length;       // 宣稱的 payload 長度
    opaque payload[payload_length];
    opaque padding[padding_length];
} HeartbeatMessage;
```

問題在 `payload_length`：client 可以宣稱 payload 有 65535 bytes，但實際只送 1 byte。

OpenSSL 的 bug：

```c
/* 有 bug 的版本（OpenSSL 1.0.1 — 1.0.1f）*/
/* 讀取 client 宣稱的 length */
n2s(p, payload);  // payload = client 宣稱的長度（例如 65535）
pl = p;           // pl 指向 client 送來的實際 data

/* 分配 response buffer */
buffer = OPENSSL_malloc(1 + 2 + payload + padding);

/* 直接用 client 宣稱的 length 做 memcpy */
memcpy(bp, pl, payload);  // ← 這裡炸了！
// pl 只有 1 byte 的實際 data，但 memcpy 複製了 65535 bytes
// 多出來的 65534 bytes 是 OpenSSL process 的其他記憶體
```

### 記憶體讀取圖

```
OpenSSL process 的記憶體
┌─────────────────────────────────────────────────────────┐
│  ...  │ Heartbeat │  Other TLS    │ Private │  Session  │
│       │ Request   │  session data │  Key    │  Tickets  │
│       │ (1 byte)  │               │         │           │
│       │◄─────────►│               │         │           │
│       │ 實際 data  │               │         │           │
│       │                                                  │
│       │◄────────────────── 65535 bytes ─────────────────►│
│       │        memcpy 複製了這整段記憶體                    │
└─────────────────────────────────────────────────────────┘
                    ↓
            Response 送回給攻擊者
            ┌──────────────────────────────┐
            │ 1 byte 原始 data +            │
            │ 65534 bytes 的 server 記憶體  │
            │ （可能包含 private key、       │
            │  session key、plaintext、     │
            │  其他使用者的 cookie...）      │
            └──────────────────────────────┘
```

### 攻擊步驟

1. 攻擊者建立一條正常的 TLS 連線
2. 送一個 Heartbeat request，宣稱 payload 長度 = 65535，實際 payload 只有 1 byte
3. Server 回送 65535 bytes——其中 65534 bytes 是 server 記憶體裡的其他資料
4. 重複幾千次，收集足夠的 memory dump
5. 從 dump 中找到 private key、session key、使用者的明文資料

### 影響

- 全球約 **17% 的 HTTPS server** 受影響（所有使用 OpenSSL 1.0.1 到 1.0.1f 的 server）
- 攻擊者可以取得：
  - Server 的 **RSA private key**（之後可以 MITM 所有連線，或解密過去錄下的流量）
  - **Session key**（解密當前連線的明文）
  - 其他使用者的 **plaintext data**（cookie、password、帳號資訊）
- 攻擊**不留痕跡**：Heartbeat 是正常的 TLS 功能，server 的 log 不會記錄異常
- Cloudflare 在 2014 年做了一個公開挑戰：能不能用 Heartbleed 拿到他們 server 的 private key。結果在挑戰發布後幾小時內就有人成功

### 修復

```c
/* 修復版本（OpenSSL 1.0.1g）*/
/* 加一行 bounds check */
if (1 + 2 + payload + 16 > s->s3->rrec.length)
    return 0;  // 宣稱的長度超過實際收到的 record 長度 → 拒絕
```

修復只需要**加一行 if**。一行 if 的缺失造成了密碼學史上影響最大的漏洞之一。

### 教訓

1. **Implementation bug 可以比密碼學弱點更致命**。AES-256 的安全等級是 2²⁵⁶ 次操作——但 Heartbleed 的攻擊成本是幾秒鐘
2. **C 語言的 memory safety 問題**是密碼學 library 最大的攻擊面。這也是為什麼 Rust 重寫（如 rustls）受到關注
3. **不使用的功能 = 攻擊面**。大部分 server 不需要 Heartbeat extension，但 OpenSSL 預設啟用了它

---

## 案例二：ROBOT（Return Of Bleichenbacher's Oracle Attack, 2017）

### 漏洞原理

1998 年 Bleichenbacher 發表了針對 RSA PKCS#1 v1.5 的 padding oracle attack。TLS 1.2 的 spec 明確要求 implementation 不能洩漏 padding 是否正確——但 2017 年 Böck 等人發現，**多家 TLS implementation 仍然洩漏了 padding 資訊**。

#### RSA PKCS#1 v1.5 加密

TLS 1.2 的 RSA key transport：client 生成 48-byte 的 pre-master secret，用 server 的 RSA public key 加密後傳送。

```
PKCS#1 v1.5 padding:
┌──────────────────────────────────────────────┐
│ 0x00 │ 0x02 │ padding_string │ 0x00 │ data  │
│      │      │ (非零 random   │      │ (48B  │
│      │      │  bytes, ≥8B)   │      │  PMS) │
└──────────────────────────────────────────────┘
```

Server 收到密文後用 RSA private key 解密，檢查 padding 格式是否正確（開頭是 `0x00 0x02`、有足夠的非零 bytes、有一個 `0x00` separator）。

#### Padding Oracle

如果 server 在 padding 不正確時的行為和 padding 正確時不同——例如不同的 error message、不同的回應時間、不同的 alert code——攻擊者就有了一個 **padding oracle**。

Bleichenbacher 的 attack 利用 RSA 的 homomorphic property（乘法同態）：

```
RSA_Enc(m₁) × RSA_Enc(m₂) = RSA_Enc(m₁ × m₂ mod N)
```

攻擊者可以把密文乘以一個精心選擇的值，讓 server 解密修改過的密文。根據 server 的回應（padding 正確 / 不正確），攻擊者可以逐步縮小 plaintext 的範圍，最終完全恢復 pre-master secret。

### 攻擊步驟

1. 攻擊者錄下一個 TLS handshake，取得 `RSA_Enc(PMS)` 密文
2. 構造一系列修改過的密文 `c' = c × s^e mod N`（s 是精心選擇的值）
3. 把每個 `c'` 作為 ClientKeyExchange 送給 server
4. 觀察 server 的回應——不同的 error 路徑會洩漏 padding 是否以 `0x00 0x02` 開頭
5. 根據 oracle 的回應，用二分搜尋法逐步縮小 PMS 的範圍
6. 經過幾千到幾百萬次 query，完全恢復 PMS
7. 用 PMS 衍生出 master secret，解密整個 TLS session

### 影響

| 受影響的廠商 | 產品 | Oracle 類型 |
|-------------|------|------------|
| F5 Networks | BIG-IP | Extra alert |
| Citrix | NetScaler | Timing |
| Cisco | ACE | Different error |
| Bouncy Castle | Java library | Exception type |
| Erlang | SSL library | Different alert |
| Facebook | 自建 TLS terminator | Timing |
| Palo Alto | PAN-OS | Extra alert |

其中 F5 和 Cisco 的 oracle 特別強（回應差異明顯），只需要幾千次 query 就能恢復 PMS。

### 修復

正確的做法（RFC 5246 Section 7.4.7.1 早就寫了，但很多人做錯）：

```python
# 正確的 RSA key exchange 處理
# 不管 padding 是否正確，都要走完完全相同的 code path

def process_client_key_exchange(encrypted_pms, rsa_private_key):
    # 解密
    decrypted = rsa_decrypt(rsa_private_key, encrypted_pms)

    # 生成隨機的 fallback PMS
    random_pms = os.urandom(48)

    # 用 constant-time comparison 檢查 padding
    # 但不管結果如何，都不能 return error 或改變行為
    padding_ok = check_pkcs1_padding(decrypted)  # constant-time
    version_ok = check_version(decrypted)         # constant-time

    # constant-time select：padding 正確就用 decrypted，否則用 random
    pms = constant_time_select(padding_ok & version_ok,
                                extract_pms(decrypted),
                                random_pms)

    # 不管走哪條路，後續的 key derivation 完全一樣
    return derive_master_secret(pms)
```

### 教訓

1. **Bleichenbacher 的 attack 在 1998 年就發表了，2017 年仍然有人做錯**——同一個 bug 重複出現近 20 年
2. **TLS 1.3 直接砍掉 RSA key transport** 是正確的決定：與其要求所有 implementation 正確處理一個本質上難以做對的 protocol，不如從根本上消除這個 attack surface
3. **Constant-time programming 比想像中難**——即使 spec 明確要求，大部分開發者還是會寫出 timing-dependent 的 code

---

## 案例三：Logjam（2015）

### 漏洞原理

1990 年代，美國政府限制密碼學出口（export control）。TLS 被迫支援「出口等級」的弱密碼學——其中 DHE_EXPORT 用 **512-bit DH group**。

2015 年的現實：512-bit DH 已經可以被 precompute（預先計算 discrete log）。而且很多 server 使用相同的 DH group（RFC 2409 和 RFC 5114 定義的幾個「標準」group）。

攻擊分兩步：

#### 第一步：Precomputation（線下）

Number Field Sieve 算法對 DH 的 discrete log 可以做 precomputation：

1. 選一個常見的 512-bit DH group
2. 花幾週到幾個月的計算時間（Adrian et al. 估計約 $1M 的硬體成本），建出一個巨大的 lookup table
3. 之後對這個 group 裡任何一個 DH 值，都能在幾十秒內算出 discrete log

#### 第二步：MITM Downgrade（線上）

```
Client                     Attacker (MITM)                  Server
──────                     ───────────────                  ──────
ClientHello
  cipher_suites:
    [DHE_RSA, ...]
───────────────→
                           篡改 ClientHello:
                             cipher_suites:
                               [DHE_EXPORT, ...]
                           ────────────────────→
                                                     ServerKeyExchange
                                                       512-bit DH params
                                                       g^b (512-bit)
                           ←────────────────────

                           用 precomputed table
                           幾十秒內算出 b
                           偽造 server 的 response

←───────────────
  ServerKeyExchange
    篡改過的 DH params

ClientKeyExchange
  g^a (512-bit)
───────────────→
                           知道 a 和 b
                           算出 shared secret
                           解密所有後續流量
```

### 影響

Adrian et al. 的研究發現：

- **8.4% 的 Alexa Top 1M HTTPS server** 支援 DHE_EXPORT
- 即使不用 EXPORT，**82% 使用 DHE 的 server** 用同一個 1024-bit DH group（Oakley Group 2, RFC 2409）
- 1024-bit DH 的 precomputation 對國家級攻擊者（NSA 級別的算力和預算）是可行的
- 他們估計 NSA 可能已經在做——這可以解釋 Snowden 洩漏的文件中 NSA 聲稱能解密大量 VPN 和 HTTPS 流量

### 修復

1. **禁用 EXPORT cipher suite**（所有 server 都該做的）
2. **用 2048-bit 或更大的 DH group**（如果還在用 DHE）
3. **改用 ECDHE**（X25519 或 P-256）——ECDHE 沒有 precomputation 的問題（Number Field Sieve 不適用於 elliptic curve discrete log）
4. **TLS 1.3 直接砍掉自定義 DH group**：只允許 named groups（x25519、secp256r1 等），不允許 server 自己選 DH 參數

### 教訓

1. **Backward compatibility 是安全的敵人**。EXPORT cipher suite 在 2015 年早就沒有存在的理由（export control 在 2000 年左右就放鬆了），但為了「相容性」仍然被很多 server 支援
2. **Shared parameters = single point of failure**。全世界 82% 的 DHE server 用同一個 DH group——一次 precomputation 就能攻破 82% 的目標
3. **Downgrade attack 利用的是 negotiation 的複雜性**。TLS 1.2 的 cipher negotiation 允許 MITM 把一個安全的連線降級成不安全的——TLS 1.3 的解法是直接不給你降級的選項

---

## 案例四：TLS Triple Handshake（2014）

### 漏洞原理

TLS 1.2 的 Finished message 用 `PRF(master_secret, "client finished", Hash(handshake_messages))` 來 bind handshake transcript。但 Bhargavan et al. 發現一個問題：**不同的 handshake 可以產生相同的 master secret**。

攻擊場景：client certificate authentication。

#### 三次握手

```
Alice                     Mallory (MITM)                  Bob
──────                     ───────────────                 ────

=== Handshake 1：Alice → Mallory ===
Alice 和 Mallory 做 RSA key exchange
  master_secret_1 = PRF(PMS, ...)
  Mallory 知道 PMS（因為她是 server 端）

=== Handshake 2：Mallory → Bob ===
Mallory 和 Bob 做 RSA key exchange
  Mallory 把同一個 PMS 傳給 Bob
  → master_secret_2 = PRF(PMS, ...)
  如果 client_random 和 server_random 都相同
  → master_secret_1 == master_secret_2 !!!

=== Handshake 3：Renegotiation ===
Bob 要求 client certificate authentication
Alice 用她的 certificate 做 CertificateVerify
  簽章的 input = handshake transcript

Mallory 把 Alice 的 CertificateVerify relay 給 Bob
  → Bob 以為 Alice 直接跟他在通訊
  → Bob 接受了 Alice 的 client certificate
  → 但實際上 Alice 是和 Mallory 在通訊
```

問題的根源：TLS 1.2 的 master secret 只取決於 `PMS + client_random + server_random`，**沒有 bind 到 DH parameter 或 server certificate**。Mallory 可以在兩個 session 之間重用同一個 PMS 和 random，導致兩個 session 有相同的 master secret。

### 攻擊步驟

1. Mallory 設立一個 MITM position
2. Mallory 和 Alice 做 Handshake 1（Mallory 扮演 Bob）——Mallory 取得 PMS
3. Mallory 和 Bob 做 Handshake 2（Mallory 扮演 Alice）——Mallory 把同一個 PMS 傳給 Bob
4. Mallory 控制 client_random 和 server_random 使兩邊的 master_secret 相同
5. Bob 要求 renegotiation + client certificate
6. Alice 做 CertificateVerify（對 handshake transcript 簽章）
7. Mallory 把 CertificateVerify relay 給 Bob
8. Bob 以為 Alice 直接通過了 client certificate authentication
9. Mallory 現在可以以 Alice 的身份存取 Bob 的受保護資源

### 影響

- 影響所有支援 renegotiation + client certificate authentication 的 TLS 1.2 server
- 實際場景：企業 VPN、mutual TLS（mTLS）、smart card authentication
- 不影響沒有 client certificate authentication 的一般 HTTPS（大部分網站）

### 修復

1. **RFC 7627 — Extended Master Secret**：修改 master secret 的計算方式，加入完整 handshake transcript 的 hash：
   ```
   原始：master_secret = PRF(PMS, "master secret",
                             client_random + server_random)
   修復：master_secret = PRF(PMS, "extended master secret",
                             Hash(handshake_messages))
   ```
   這樣不同的 handshake 就不會產生相同的 master secret

2. **TLS 1.3 的根本解法**：
   - 砍掉 renegotiation——根除攻擊的前提條件
   - Finished message 綁定完整的 handshake transcript（HKDF key schedule 的每一步都包含 transcript hash）
   - 砍掉 RSA key transport——沒有 PMS 的直接傳遞，Mallory 不能在兩個 session 之間重用 PMS

### 教訓

1. **Handshake transcript binding 為什麼重要**：master secret 必須綁定到完整的 handshake 過程，而不是幾個 random 值
2. **Renegotiation 是一個持續的攻擊面**：這是 TLS 1.2 被反覆攻擊的功能——Triple Handshake 之前還有 2009 年的 Renegotiation Attack（CVE-2009-3555）
3. **Protocol 的安全分析不能只看「正常使用」**：Triple Handshake 利用的是多個 session 之間的交互——單看一個 session 完全合法

---

## 對比與取捨

### 四個漏洞對比表

| 面向 | Heartbleed | ROBOT | Logjam | Triple Handshake |
|------|-----------|-------|--------|-----------------|
| CVE | CVE-2014-0160 | CVE-2017-13099 等 | CVE-2015-4000 | 無 CVE（設計問題） |
| 年份 | 2014 | 2017 | 2015 | 2014 |
| 影響版本 | OpenSSL 1.0.1–1.0.1f | 多家 TLS 實作 | 支援 DHE_EXPORT 的 server | 支援 renegotiation 的 TLS 1.2 |
| 根本原因 | Implementation bug（缺少 bounds check） | Implementation bug（padding oracle 洩漏） | Protocol design（允許弱 DH + downgrade） | Protocol design（缺少 transcript binding） |
| 攻擊前提 | 能和 server 建 TLS 連線 | 能和 server 建多次 TLS 連線 | MITM position + precomputation | MITM position |
| 攻擊成本 | 極低（幾秒） | 中（幾千到幾百萬次 query） | 高（precompute 花數週/月） | 中 |
| 洩漏的資料 | Server 記憶體（private key、plaintext） | Pre-master secret | Session key（透過 MITM） | Client certificate identity |
| 修復方式 | 一行 bounds check | Constant-time padding 處理 | 禁用 EXPORT + 用大 DH group | Extended Master Secret (RFC 7627) |
| TLS 1.3 的根本解法 | 無直接關聯（implementation bug） | 砍掉 RSA key transport | 砍掉自定義 DH group + EXPORT | 砍掉 renegotiation + transcript binding |

### Implementation Bug vs Protocol Design Flaw

| 面向 | Implementation Bug | Protocol Design Flaw |
|------|-------------------|---------------------|
| 代表案例 | Heartbleed, ROBOT | Logjam, Triple Handshake |
| 修復難度 | 容易（patch 一行 code） | 困難（需要改 spec、所有 implementation 跟進） |
| 影響範圍 | 特定 library 的特定版本 | 所有符合 spec 的 implementation |
| 預防方式 | Code review, fuzzing, memory-safe 語言 | Formal verification, 保守的 design（少 feature） |
| 重複出現？ | ROBOT 是 1998 年 Bleichenbacher 的重現 | Triple Handshake 是 2009 年 Renegotiation Attack 的延伸 |

---

## 踩雷集錦

### 踩雷 1：「用最新版 TLS 就安全」

TLS 1.3 的 protocol design 解決了 Logjam 和 Triple Handshake 的根本原因。但：

- **Server 的 configuration 可能允許 downgrade**：如果 server 同時支援 TLS 1.2 和 1.3，攻擊者可能嘗試 downgrade（TLS 1.3 有 downgrade sentinel 防禦，但需要正確 implementation）
- **Implementation bug 仍然存在**：Heartbleed 是 implementation bug——用 TLS 1.3 的 OpenSSL 如果有類似的 bug，一樣會被打
- **Side channel 仍然存在**：timing oracle 在 TLS 1.3 裡仍然可能出現（例如 certificate verification 的 timing）

### 踩雷 2：Heartbleed 不是密碼學漏洞——但密碼學課必須教

Heartbleed 不涉及任何密碼學弱點。它是一個 bounds check 的 bug。但它洩漏的是 **密碼學 key**——private key、session key、pre-master secret。

教訓：密碼學的安全性不只取決於演算法——**承載演算法的 code 和環境同等重要**。

### 踩雷 3：「RSA 已經沒人用了」

截至 2025 年，大量的 TLS server 仍然支援 TLS 1.2 + RSA key exchange（為了相容老舊的 client）。ROBOT 的影響在 2017 年是巨大的——因為「沒人用」和「server 支援但沒人選」是兩回事。MITM 可以強迫 client 選 RSA key exchange。

**行動項目**：如果你管理 TLS server，用 `testssl.sh` 或 `ssllabs.com/ssltest` 檢查你的 server 是否仍然支援 RSA key exchange，考慮停用。

### 踩雷 4：「我用 Let's Encrypt 所以安全」

Let's Encrypt 解決的是 certificate 的取得問題，不是 protocol 的安全問題。你的 server 用什麼 cipher suite、是否允許 downgrade、OpenSSL 版本是否有漏洞——這些和 certificate authority 無關。

---

## 進階

### TLS 漏洞偵測工具

```bash
# testssl.sh — 最全面的 TLS 掃描工具
git clone https://github.com/drwetter/testssl.sh.git
./testssl.sh --vulnerable target.com:443
# 一次檢查：Heartbleed、ROBOT、POODLE、Logjam、FREAK、DROWN、
# BEAST、CRIME、BREACH、Lucky 13、Sweet32 等全部已知漏洞

# 單項偵測
nmap -p 443 --script ssl-heartbleed target.com   # Heartbleed
python3 robot-detect.py -t target.com              # ROBOT（robotattackorg/robot-detect）
```

### 為什麼 Memory-Safe 語言對密碼學重要

Heartbleed 的根本原因是 C 語言的 memory unsafety。用 Rust 或 Go 重寫 TLS library 可以消除整類 bug：

| Library | 語言 | Heartbleed-class bug 可能？ |
|---------|------|--------------------------|
| OpenSSL | C | 可能 |
| BoringSSL | C（Google fork of OpenSSL） | 可能（但 code review 更嚴格） |
| rustls | Rust | 不可能（Rust 的 borrow checker 阻止 out-of-bounds read） |
| Go crypto/tls | Go | 不可能（Go 有 bounds checking） |

rustls 的 CVE 數量遠低於 OpenSSL——不是因為 rustls 的開發者更聰明，而是因為 **Rust 從語言層面消除了整類 bug**。

### Downgrade Sentinel（TLS 1.3）

TLS 1.3 在 ServerHello 的 `server_random` 最後 8 bytes 放 sentinel `"DOWNGRD" + 0x01`（降級到 1.2）或 `+ 0x00`（降級到 1.1 以下）。支援 TLS 1.3 的 client 收到 TLS 1.2 的 ServerHello 時會檢查 sentinel——如果存在，代表 server 被 MITM downgrade 了，client 中斷連線。

---

## 動手練習

1. **Heartbleed 偵測腳本**：用 Python 的 `socket` 和 `struct` 模組寫一個 Heartbleed 偵測工具。構造一個 Heartbeat request，送給 target server，檢查 response 的長度是否超過 request 的 payload 長度。（注意：只對自己的 test server 跑）

2. **TLS server 安全掃描**：用 `testssl.sh` 掃描三個不同的 HTTPS server（例如你自己的 server、github.com、一個已知較舊的 server），比較它們支援的 cipher suite 和已知漏洞

3. **Downgrade sentinel 觀察**：用 `openssl s_client -max_proto TLSv1.2` 連到一個支援 TLS 1.3 的 server，dump ServerHello 的 `server_random`，檢查最後 8 bytes 是否是 downgrade sentinel

4. **（挑戰）Bleichenbacher padding oracle 模擬**：用 Python 實作一個「有 bug 的 RSA PKCS#1 v1.5 padding checker」（會洩漏 padding 是否正確），然後寫一個攻擊者腳本利用 oracle 恢復 plaintext。提示：先對 256-bit RSA 做（夠小、跑得動）

---

## 重點整理

1. **Heartbleed**：一行缺失的 bounds check → 讀取 server 記憶體 → 洩漏 private key。Implementation bug 可以比密碼學弱點更致命
2. **ROBOT**：1998 年的 Bleichenbacher attack 在 2017 年重現——TLS 1.2 的 RSA PKCS#1 v1.5 padding oracle 仍然被多家廠商做錯。TLS 1.3 砍掉 RSA key transport 是正確的
3. **Logjam**：512-bit DH 的 precomputation + MITM downgrade。Backward compatibility 是安全的敵人
4. **Triple Handshake**：TLS 1.2 的 master secret 沒有 bind handshake transcript → client certificate relay。TLS 1.3 砍掉 renegotiation + 完整 transcript binding 解決了
5. **共同教訓**：密碼學的安全性取決於最弱的環節——protocol design、implementation quality、configuration 三者缺一不可

---

## 自我檢核

1. Heartbleed 的根本原因是什麼？它洩漏了什麼？為什麼攻擊不留痕跡？
2. ROBOT 和 1998 年的 Bleichenbacher attack 有什麼關係？TLS 1.3 怎麼從根本上防止這類攻擊？
3. Logjam 為什麼需要 precomputation？為什麼大部分 server 用同一個 DH group 會讓問題更嚴重？
4. Triple Handshake 利用了 TLS 1.2 master secret 計算的什麼缺陷？Extended Master Secret（RFC 7627）怎麼修復？
5. 為什麼「用最新版 TLS 就安全」是錯誤的想法？安全性的三個層級是什麼？
6. Memory-safe 語言（Rust、Go）為什麼能防止 Heartbleed 類型的 bug？

---

## 延伸閱讀

- **Heartbleed**
  - heartbleed.com — 漏洞的官方說明頁面
  - Synopsys, "The Heartbleed Bug" — 技術細節和 timeline
  - Durumeric et al., "The Matter of Heartbleed"（IMC 2014）— 對 Heartbleed 影響的大規模實證研究

- **ROBOT**
  - robotattack.org — 漏洞說明和偵測工具
  - Böck et al., "Return Of Bleichenbacher's Oracle Threat (ROBOT)"（USENIX Security 2018）— 完整的研究論文

- **Logjam**
  - "Imperfect Forward Secrecy: How Diffie-Hellman Fails in Practice"（Adrian et al., CCS 2015）— Logjam 的原始論文，同時估計了 NSA 的能力
  - weakdh.org — 漏洞說明和 server 檢測工具

- **Triple Handshake**
  - "Triple Handshakes and Cookie Cutters: Breaking and Fixing Authentication over TLS"（Bhargavan et al., IEEE S&P 2014）— 原始論文
  - RFC 7627 — Extended Master Secret Extension for TLS — 修復方案

- **綜合**
  - testssl.sh（https://testssl.sh/）— 最全面的 TLS 漏洞掃描工具
  - SSL Labs Server Test（https://www.ssllabs.com/ssltest/）— Qualys 的線上 TLS 檢測

---

## 下一章預告

[Ch 38 — Side-Channel Attack](./38-side-channel.md)：前面四個漏洞攻擊的是 protocol 邏輯或 implementation 的功能性 bug。下一章攻擊的是更隱蔽的東西——**物理世界的資訊洩漏**。執行時間、功耗、電磁輻射、cache 存取 pattern——這些「不該是 output 的東西」全都可以洩漏密碼學 key。
