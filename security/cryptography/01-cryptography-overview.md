# Ch 1 — 密碼學全貌：學科 vs 工程

> 目標：在動手寫 modular arithmetic 之前，先把密碼學的 landscape 釐清。學科（cryptography）vs 工程（cryptographic engineering）的分野、Kerckhoffs 原則、為什麼自己 roll crypto 是壞主意（但學習用 OK）、本課的整體地圖。

## 密碼學在做什麼

簡化到極致一句：**讓 Alice 與 Bob 通訊時，Eve 偷聽 / 篡改 / 偽造都辦不到**。

```
                    ┌──────┐                ┌──────┐
        plaintext   │      │   ciphertext   │      │   plaintext
   ─── Alice ──────▶│ Enc  │ ─────────────▶│ Dec  │ ─────▶ Bob
                    └──────┘                └──────┘
                       ▲                        ▲
                       └─────── key ────────────┘
                                                
                            ↑
                 Eve 看 / 改 / 重送
```

但「辦不到」要對應具體的安全模型 — Eve 是被動聽（passive eavesdrop）還是能主動改（active MITM）？她有預算 $100 還是 $10⁹？她有量子電腦嗎？

**密碼學的核心是**：在明確的**威脅模型**（threat model）下，給出**可證明**或**最強已知**的安全保證。

## 五件事密碼學處理

| 性質 | 名稱 | 例子 |
|---|---|---|
| **保密** | Confidentiality | AES 加密訊息，Eve 看到亂碼 |
| **完整** | Integrity | HMAC 防止 Eve 改一個 byte 後 Bob 沒發現 |
| **驗證** | Authentication | 簽章證明訊息是 Alice 寫的 |
| **抗重放** | Anti-replay | nonce / counter 防 Eve 重送過去訊息 |
| **不可否認** | Non-repudiation | 簽章後 Alice 不能說「不是我簽的」 |

很多新手以為密碼學 = 加密。**錯**。加密只是其中一件，多數安全事件是 integrity / authentication 出問題（cookie tampering、JWT alg=none、Heartbleed memory leak）。

## 學科 vs 工程：兩條軌道

```
密碼學 (Cryptography)              密碼工程 (Cryptographic Engineering)
─────────────────                  ──────────────────────────────────
研究：演算法 / protocol            實作 / 部署 / 運維
產出：論文 (eprint.iacr.org)       產出：library / 系統
場景：學術會議 (CRYPTO, EUROCRYPT) 場景：OpenSSL, libsodium, Cloudflare
語言：數學 + 安全證明              語言：C / Rust / Go + 工程紀律
代表：Boneh, Bellare, Bernstein    代表：Bert/Tanja, Aumasson, Filippo
取捨：嚴謹 > 速度                  取捨：速度 + 安全 + 兼容性
```

兩個不同職業，但不能完全分。**好的密碼工程師要讀得懂論文**（看出 paper 在你 production 系統的真實意義）；**好的密碼學者要懂工程**（不然 paper 永遠是 paper）。

本課**偏工程，補足必要的學術基礎**：你不會去做 reduction proof，但會看得懂 IND-CPA 定義、會比較 ECDSA / EdDSA 設計上的差異、會在 CVE 出來時看懂 paper。

## Kerckhoffs 原則：1883 至今

法國軍人 Auguste Kerckhoffs 1883 年的命題：

> **「密碼系統的安全性必須只依賴密鑰的保密，不能依賴演算法本身的保密。」**

意思：**演算法可以公開，重要的是 key**。如果你的安全只靠「敵人不知道我用什麼演算法」（security through obscurity），你就完了 — 演算法早晚被逆向、洩漏、退休工程師爆料。

延伸到現代：

- **AES、SHA、Curve25519 全部規格公開** — 任何人能讀
- **OpenSSL、libsodium 全部 source 公開** — 任何人能 audit
- **NIST、IETF 制訂標準走公開審查** — 多年論文戰才能 ratified

這個原則是現代密碼學的根。**不公開的演算法基本沒人用**（DRM 級的東西反而是 worst-of-both-worlds）。

## 為什麼自己 roll crypto 是壞主意

「我會數學，AES 也不難，自己寫一個吧」 — 這想法每個工程師都會有，幾乎都會踩雷。

實際發生過的事：

- **side-channel 漏洞**：你寫的 const-time RSA，編譯器把 const-time branch 優化掉
- **隨機數錯**：用 `rand()` 而非 `/dev/urandom`，或 seeded 不夠 entropy
- **padding 攻擊**：CBC 寫對了，但 padding 檢查回傳不同錯誤，被 Vaudenay 殺掉
- **nonce reuse**：AES-GCM 寫對了，但同個 key 重用 nonce → 整個 GHASH key 被算出
- **timing leak**：`memcmp` 比 MAC，攻擊者用 timing side-channel 一個 byte 一個 byte 試出來

**寫對演算法只是入場票**。整個攻擊面包含：實作（const-time）、編譯器優化、CPU 微架構（cache、speculative execution）、protocol 設計、key management、entropy 來源 — 任一環錯就完蛋。

audited library（libsodium、ring、Tink）背後有**多人多年累積**處理這些 corner case。**你一個人寫不過他們**。

但**學習用自己刻**完全 OK。這門課的 deliverable 不是 production code，是「**讀得懂 OpenSSL**」「**Heartbleed 出現知道是哪一行**」「**CTF 出 padding oracle 你能秒解**」。

## 規範與標準的世界

密碼學標準有幾個主要組織：

```
NIST (National Institute of Standards and Technology, 美國)
  ├── FIPS 197: AES
  ├── FIPS 180-4: SHA family
  ├── FIPS 186-5: ECDSA, EdDSA
  ├── FIPS 202: SHA-3
  ├── FIPS 203: ML-KEM (Kyber)
  ├── FIPS 204: ML-DSA (Dilithium)
  └── FIPS 205: SLH-DSA (SPHINCS+)

IETF (Internet Engineering Task Force, 通用)
  ├── RFC 8446: TLS 1.3
  ├── RFC 8439: ChaCha20-Poly1305
  ├── RFC 7748: Curve25519, Curve448
  ├── RFC 5869: HKDF
  └── RFC 9106: Argon2

ISO / IEC（國際標準）
  較少被工程師直接用，多數和 NIST / IETF 重疊

CFRG (Crypto Forum Research Group, IETF 的研究組)
  推 modern primitive 進 IETF 流程

學術社群
  IACR（International Association for Cryptologic Research）
  ePrint server: <https://eprint.iacr.org>
```

實務上：**先看 IETF RFC**（最貼近工程實作）、**再看 NIST FIPS**（規範細節）、**疑問才查 IACR paper**（理論證明）。

## Trust：誰決定什麼是安全的？

「AES 安全嗎？」 — 沒有絕對答案，但有共識機制：

1. **演算法公開**（Kerckhoffs）
2. **長時間多人嘗試攻擊都沒結果**（cryptanalysis 社群每年多場會議）
3. **NIST / IETF 標準化過程**（多年公開審查）
4. **被 production 大規模採用**（Google / Cloudflare / OpenSSH 用了多年沒事）

到這四件事都成立 = **業界共識相信這是安全的**。但這是**機率性**信任，不是數學證明 — 多數密碼演算法的安全是「**目前沒人破得了**」，不是「**證明永遠破不了**」。

例外：OTP（Ch 6）有 Shannon 的數學證明，但工程上沒用。**現實密碼學接受 computational security**：在合理計算資源下沒人破得了 = 算數學上「可信」。

## 攻防演化簡史

```
1976  Diffie-Hellman 公鑰革命
1977  RSA、DES（NIST）
1985  Koblitz / Miller 提橢圓曲線
1990s SSL / TLS、PGP、Web 加密
2001  AES 標準化（Rijndael 贏得競賽）
2008  比特幣（密碼學進入金融主流）
2013  Snowden 揭密：NSA Bullrun 計畫滲透標準（Dual_EC_DRBG）
2014  Heartbleed
2017  SHA-1 collision (Google)
2018  Curve25519 / Ed25519 進主流
2022  NIST PQC 公布獲選名單
2024  FIPS 203/204/205 正式標準化（後量子）
```

**現代密碼學從沒停過攻防演化**。學完這門課，你會看新聞而非看新聞被嚇到。

## 本課的 promise 與 anti-promise

**Promise**：
- 看 OpenSSL `crypto/aes/aes_core.c` 不會懵
- 看 RFC 8446 (TLS 1.3) 知道每個 message 在做什麼
- 看 CVE-2017-15361（ROCA）能說「啊這是 Coppersmith 的問題」
- 學完能讀 IACR ePrint 上的論文（雖然不一定全懂）

**Anti-promise**：
- 不會讓你成為密碼學者（那需要 PhD）
- 不會讓你能設計新演算法（那是 attack-resistant design 的研究）
- 不會涵蓋每個 protocol 細節（TLS 1.2 / IPsec / Kerberos 各能寫一本書）

## 一個常見誤解

「我用 Python `cryptography` 就好，為什麼要學 internals？」

工程上你**確實該用 library，不該自己刻**。但學 internals 的價值：

- **debug** 用 library 出錯時，error message 看得懂在說什麼
- **選擇** 用 library 時知道 GCM vs ChaCha20-Poly1305 該選誰
- **CVE / 安全事件** 出現時，看新聞知道你公司用的東西有沒有受影響
- **CTF / 滲透**：很多 challenge 是「給弱實作的密碼系統，破解」
- **面試**：senior security 職位常問 「為什麼 ECDSA 重用 nonce 會死」「padding oracle 怎麼運作」

不學 internals 你永遠是「會用 library 的人」 — 行業需要的是「**懂 library 的人**」。

## 自我檢核

- [ ] 我能說出密碼學處理的五件事
- [ ] 我能解釋 Kerckhoffs 原則並舉例
- [ ] 我能說出兩個自己 roll crypto 會踩的雷
- [ ] 我知道 NIST FIPS 與 IETF RFC 各自負責哪類規範
- [ ] 我能解釋為什麼 production 一定要用 audited library

下一章開始 Part 1，從零教必要的數論。modular arithmetic、ext-Euclid、CRT — 後面 RSA 與 DH 會反覆用這幾個工具。

→ [Ch 2 數論速覽](./02-number-theory.md)
