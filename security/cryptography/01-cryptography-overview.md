# Ch 1 — 密碼學全貌

> **目標**：建立密碼學六大原語的心智模型——對稱加密、非對稱加密、雜湊、MAC、數位簽章、KDF 各自做什麼、怎麼組合；理解 Kerckhoffs' principle；記住「不要自己寫 crypto」這條鐵律背後的真實教訓。

## 六大原語的關係圖

密碼學的所有工具可以歸納為六大原語（primitive），它們各自解決不同的安全問題，也經常組合使用：

```
                        ┌─────────────┐
                        │   KDF       │
                        │ (密鑰導出)  │
                        └──────┬──────┘
                               │ 產生 key
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
     ┌────────────────┐ ┌──────────┐  ┌──────────────────┐
     │ 對稱加密       │ │  MAC     │  │ 非對稱加密        │
     │ (AES, ChaCha20)│ │ (HMAC)  │  │ (RSA, ECDH)      │
     │                │ │          │  │                   │
     │ 機密性         │ │ 完整性   │  │ 機密性 + 金鑰交換  │
     │ Confidentiality│ │ Integrity│  │ Key Exchange      │
     └────────┬───────┘ └────┬─────┘  └────────┬──────────┘
              │              │                  │
              │    ┌─────────┘                  │
              ▼    ▼                            ▼
     ┌────────────────┐               ┌──────────────────┐
     │  AEAD          │               │  數位簽章         │
     │ (AES-GCM,      │               │ (RSA-PSS, ECDSA, │
     │  ChaCha20-     │               │  EdDSA)          │
     │  Poly1305)     │               │                   │
     │                │               │ 完整性 + 認證 +   │
     │ 機密性 + 完整性│               │ 不可否認性        │
     └────────────────┘               └──────────────────┘
              │                                │
              └──────────┬─────────────────────┘
                         ▼
              ┌──────────────────┐
              │     Hash         │
              │  (SHA-256,       │
              │   SHA-3)         │
              │                  │
              │ 單向壓縮         │
              │ (被其他原語使用) │
              └──────────────────┘
```

每個原語的一句話定義：

| 原語 | 做什麼 | 安全屬性 | 代表演算法 |
|---|---|---|---|
| 對稱加密 | 用同一把 key 加密和解密 | Confidentiality | AES-256, ChaCha20 |
| 非對稱加密 | 公鑰加密、私鑰解密；或 DH 交換共享密鑰 | Confidentiality, Key Exchange | RSA, ECDH, X25519 |
| Hash | 任意長度輸入 → 固定長度輸出，單向不可逆 | 被其他原語當作building block | SHA-256, SHA-3, BLAKE2 |
| MAC | 用 key + message 產生 authentication tag | Integrity, Authenticity | HMAC-SHA256, Poly1305 |
| 數位簽章 | 用私鑰簽署、公鑰驗證 | Integrity, Authenticity, Non-repudiation | RSA-PSS, ECDSA, EdDSA |
| KDF | 從 password 或 master key 導出密鑰 | Key Derivation | HKDF, Argon2, scrypt |

注意它們的**組合關係**：
- **AEAD = 對稱加密 + MAC**（一步完成加密和認證，現代的標準做法）
- **HMAC = Hash + key**（用 hash 函式建構 MAC）
- **數位簽章 = Hash + 非對稱運算**（先 hash message 再用私鑰簽 hash）
- **TLS = ECDH（金鑰交換）+ AEAD（資料加密）+ 數位簽章（身份認證）+ KDF（密鑰導出）**

## 四個安全屬性

密碼學保護四件事，每件對應不同的原語：

### Confidentiality（機密性）
只有授權方能讀取內容。
- **用什麼**：對稱加密（AES-GCM）或非對稱加密（RSA）
- **不保證**：內容沒被篡改（加密 ≠ 完整性保護）

### Integrity（完整性）
內容沒有被篡改。
- **用什麼**：MAC（HMAC）或數位簽章
- **不保證**：內容是誰送的（完整性 ≠ 認證）

### Authenticity（認證性）
確認內容來自宣稱的發送者。
- **用什麼**：MAC（雙方共享 key）或數位簽章（公鑰驗證）
- MAC 和簽章的差別：MAC 是對稱的——雙方都能產生和驗證；簽章是非對稱的——只有私鑰持有者能簽

### Non-repudiation（不可否認性）
簽署者無法事後否認自己簽過。
- **用什麼**：只有數位簽章能做到
- **為什麼 MAC 做不到**：MAC 用共享 key，雙方都能產生 tag——你說是對方簽的，對方說是你簽的，無法仲裁

## Kerckhoffs' Principle

1883 年，Auguste Kerckhoffs 提出：

> **密碼系統的安全性應該完全依賴 key 的保密，而非演算法的保密。**

現代的說法：假設攻擊者知道你的演算法的每一個細節（原始碼、硬體設計、protocol 規格），系統仍然安全——只要 key 沒洩漏。

這為什麼重要：

1. **演算法難以保密**：逆向工程、洩漏、員工離職——演算法的保密性是脆弱的。key 是一個短字串，可以定期更換；演算法是整個系統，換不了
2. **公開審查讓演算法更強**：AES 是公開競賽的產物（1997–2000），全世界密碼學家嘗試破解候選演算法，最後存活的才成為標準。Kerckhoffs' principle 逼你把安全性建立在數學困難性上，而非隱蔽性上
3. **反面教材是 Security through Obscurity**：GSM 的 A5/1 加密演算法曾經保密，被逆向後在幾秒內可破解。如果一開始就公開審查，這個問題會在部署前被發現

## 為什麼不要自己寫 Crypto

這不是建議，是鐵律。三個真實災難案例：

### 案例 1：PlayStation 3 的 ECDSA — 重複 nonce

2010 年，fail0verflow 團隊破解了 PS3 的 code signing。Sony 的實作在 ECDSA 簽章時使用了固定的 nonce（隨機數 k），而非每次簽章用不同的隨機值。

ECDSA 的簽章公式裡，如果兩次簽章用同一個 k，攻擊者可以從兩個簽章直接算出私鑰。數學上：

```
s1 = k^(-1) * (H(m1) + r * d) mod n
s2 = k^(-1) * (H(m2) + r * d) mod n
```

兩式相減，k 是已知的（因為相同），d（私鑰）可以直接解出。Sony 用的不只是重複的 nonce——他們用了一個常數。結果：PS3 的 master signing key 被公開，任何人都能簽署可在 PS3 上執行的程式碼。

**教訓**：ECDSA 對 nonce 重複的敏感性是演算法的數學性質，不是實作細節。自己寫 ECDSA 時「忘記用隨機 nonce」或「RNG 壞了」是致命的。

### 案例 2：Heartbleed — OpenSSL 的記憶體洩漏

2014 年，OpenSSL 1.0.1 的 TLS Heartbeat extension 實作有一個 buffer over-read 漏洞（CVE-2014-0160）。攻擊者送一個聲稱 payload 長度為 65535 bytes 但實際只有 1 byte 的 heartbeat request，OpenSSL 會把 server 記憶體裡的 64KB 內容（可能包含私鑰、session key、使用者密碼）回傳。

這不是密碼學演算法的問題——TLS 的數學是對的——而是 C 語言實作中缺少長度驗證。一行 bounds check 就能防止。

**教訓**：密碼學工程不只是演算法正確。記憶體安全、bounds checking、error handling 都是攻擊面。OpenSSL 是全世界最多眼睛看的密碼學程式碼之一，這個 bug 仍然存活了兩年。你自己寫的 code 會有多少眼睛看？

### 案例 3：WEP — 設計層面的失敗

IEEE 802.11 的 WEP（Wired Equivalent Privacy）在 2001 年被徹底破解。問題不只一個：

- RC4 的 key scheduling 有 weak key 問題，WEP 直接把 24-bit IV 和 key 串接（而非用 KDF），導致相關 key attack
- 24-bit IV 空間太小（2^24 ≈ 1600 萬），在繁忙網路上幾小時就會重複
- CRC-32 被用來做 integrity check——CRC 不是密碼學 hash，攻擊者可以修改封包內容並重新計算 CRC

結果：`aircrack-ng` 可以在幾分鐘內從擷取的封包中還原 WEP key。

**教訓**：WEP 不是某一行 code 寫錯——是整個 protocol 設計者不理解密碼學原語的正確組合方式。RC4 本身不是罪——把 IV 和 key 串接是罪。CRC-32 本身不是罪——把它當 MAC 用是罪。

### 共同教訓

三個案例的問題分別在不同層次：
- **演算法使用錯誤**（PS3：nonce 重複）
- **實作漏洞**（Heartbleed：buffer over-read）
- **協議設計缺陷**（WEP：錯誤的原語組合）

自己寫 crypto 意味著你要同時在這三個層次都不犯錯。用經過審計的程式庫（`cryptography`、libsodium、OpenSSL）是因為那些程式庫已經被全世界的密碼學家和安全研究員審查過。你的 code 沒有這個待遇。

## 密碼學 vs 密碼分析 vs 密碼工程

這三個詞經常被混用，但它們是不同的專業：

### 密碼學（Cryptography）
設計密碼系統。
- 工作內容：定義安全模型、證明演算法在該模型下安全、設計新的原語
- 需要的技能：數學（數論、代數、機率論）、形式化證明
- 代表人物：Dan Boneh、Shafi Goldwasser、Whitfield Diffie

### 密碼分析（Cryptanalysis）
破解密碼系統。
- 工作內容：找到演算法的數學弱點、降低安全強度、實際破解
- 需要的技能：數學 + 創造力 + 耐心
- 代表人物：Adi Shamir（差分密碼分析）、Mitsuru Matsui（線性密碼分析）

### 密碼工程（Cryptographic Engineering）
正確地實作密碼系統。
- 工作內容：把演算法變成安全的 code、防 side-channel、管理金鑰生命週期
- 需要的技能：C/Rust 程式設計、組合語言、硬體知識
- 代表人物：djb（Daniel J. Bernstein）、Adam Langley（Google, BoringSSL）

本課三者都碰：Part 1–6 學密碼學（原語的設計邏輯），Part 9 學密碼分析（攻擊方法），Part 8 學密碼工程（協議實作）。但本課不會做嚴格的 reduction proof——那是研究所層級的密碼學。

## 安全屬性 vs 原語的對應（速查表）

```
你需要什麼？                 用什麼？
─────────────────────────────────────────────────────
「別人不能讀」               → 加密（對稱 or 非對稱）
「確認沒被改過」             → MAC 或簽章
「確認是誰送的」             → MAC（共享 key）或簽章（公鑰驗證）
「對方不能否認」             → 數位簽章（MAC 做不到）
「從 password 導出 key」     → KDF（Argon2, scrypt）
「從 master key 導出多個 key」→ HKDF
「一步加密 + 認證」          → AEAD（AES-GCM, ChaCha20-Poly1305）
「金鑰交換」                 → DH / ECDH / X25519
「驗證檔案沒被改」           → Hash（但要注意 hash 不防偽造——需要簽章）
```

## 現代密碼學的演進時間線

建立歷史感有助於理解「為什麼現在的標準是這樣」：

```
1949  Shannon — "Communication Theory of Secrecy Systems"
      定義 perfect secrecy，密碼學從技藝變成數學

1976  Diffie & Hellman — "New Directions in Cryptography"
      公鑰密碼學誕生，解決 key distribution 問題

1977  DES 成為美國標準（FIPS 46）
      第一個官方標準化的對稱密碼；56-bit key 在當時夠用

1978  RSA 發表
      第一個實用的公鑰加密 + 數位簽章方案

1991  PGP（Pretty Good Privacy）發佈
      密碼學走出學術圈，進入一般使用者手中

2001  AES 成為標準（FIPS 197）
      取代 DES；Rijndael 在公開競賽中勝出

2008  Bitcoin 白皮書（密碼學的大規模應用）
      SHA-256 + ECDSA 在 P2P 系統中的工程實踐

2013  Snowden 揭露 NSA 監控
      推動 TLS everywhere 運動；HTTPS 從「有就好」變成「必須」

2014  Heartbleed（OpenSSL CVE-2014-0160）
      密碼學工程的教訓——正確的數學救不了壞的實作

2017  TLS 1.3 標準化（RFC 8446）
      移除所有不安全的選項；1-RTT handshake

2024  NIST Post-Quantum 標準定案
      ML-KEM (Kyber)、ML-DSA (Dilithium)、SLH-DSA (SPHINCS+)
```

注意這條時間線的三個轉折：

1. **1976 — 從對稱到非對稱**：Diffie-Hellman 之前，雙方要安全通訊必須先線下交換 key。之後，兩個陌生人可以在公開頻道上建立共享秘密
2. **2001 — 從保密到公開競賽**：AES 的誕生是 Kerckhoffs' principle 的最佳實踐——公開所有候選演算法，讓全世界密碼學家嘗試破解，最後存活的成為標準
3. **2024 — 從經典到 Post-Quantum**：量子電腦（如果大規模造出來）會破解 RSA 和 ECC，NIST 標準化的 lattice-based 方案是預防措施

## 踩雷集錦

### 1. 「加密 = 安全」

加密只保護 confidentiality。如果你加密了一段訊息但沒有 MAC，攻擊者可以翻轉密文的某些 bit——解密後的明文會被篡改，而你不會發現。這就是為什麼現代做法是 AEAD（加密 + 認證一步完成），而非單獨的 AES-CBC。

Ch 11（Padding Oracle）會詳細示範：AES-CBC 加密的密文如何被攻擊者在不知道 key 的情況下篡改成有意義的內容。

### 2. 「AES 不可破」

AES 的數學在目前是安全的——沒有已知的比暴力搜索好太多的攻擊。但 AES 的**實作**可以被 side-channel 攻擊打穿：cache-timing attack 可以從 AES 查表操作的時間差異中推出 key。

安全的 AES 實作要用 constant-time 的查表（或硬體 AES-NI 指令），Ch 38–39 會深入。

### 3. 「越長的 key 越安全」

AES-128 和 AES-256 的安全性差異在實務上不是決定因素。protocol 層面的錯誤（nonce 重複、padding oracle、不驗 MAC）比 key 長度帶來的威脅大幾個數量級。

把 AES-128 換成 AES-256 花不了多少功夫，但修好 protocol misuse 可能要重新設計整個系統。先把 protocol 搞對再煩惱 key 長度。

### 4. 「Hash 可以驗證檔案完整性」

只有一半對。如果你從官方網站下載 SHA-256 hash 和檔案，hash 可以驗證「檔案沒有在傳輸中被改」。但如果攻擊者能同時修改網站上的 hash 和檔案——hash 就沒用了。你需要的是**數位簽章**（用發布者的私鑰簽署 hash），這樣即使攻擊者篡改了網站，驗證會失敗。

Linux 發行版的套件管理（apt, yum）用的就是 GPG 簽章，不是裸 hash。

## 本章重點整理

- 密碼學六大原語：對稱加密、非對稱加密、Hash、MAC、數位簽章、KDF——各自解決不同的安全問題
- 四個安全屬性：Confidentiality、Integrity、Authenticity、Non-repudiation——不同原語保護不同屬性
- Kerckhoffs' principle：安全性依賴 key 的保密，不依賴演算法的保密
- 不要自己寫 crypto：演算法使用錯誤（PS3）、實作漏洞（Heartbleed）、協議設計缺陷（WEP）三個層面都可能出事
- 現代標準做法是 AEAD（AES-GCM 或 ChaCha20-Poly1305），不要單獨用 AES-CBC

## 自我檢核

- [ ] 能畫出六大原語的關係圖（哪些是 building block、哪些是組合）
- [ ] 能解釋 MAC 和數位簽章的根本區別（對稱 vs 非對稱、non-repudiation）
- [ ] 能用自己的話說 Kerckhoffs' principle，並舉一個反面案例
- [ ] 能解釋為什麼「加密 ≠ 安全」——加密不保護 integrity
- [ ] 能說出三個「自己寫 crypto」的災難案例，每個的問題在哪一層

## 延伸閱讀

### 書籍

- **《Serious Cryptography》2nd ed — Ch 1** — Aumasson
  - **讀哪裡**：整個 Ch 1（Encryption）
  - **學什麼**：和本章相同的概觀，但 Aumasson 的寫法更偏工程角度；他用不同的災難案例
  - **前提**：無

- **《A Graduate Course in Applied Cryptography》— Ch 1** — Boneh & Shoup
  - **讀哪裡**：Ch 1.1–1.4（Introduction）
  - **學什麼**：更嚴格的安全模型定義；本章講直覺，Boneh 講形式化
  - **前提**：基礎離散數學

### 部落格

- **["Why Not Roll Your Own Crypto" — Trail of Bits](https://blog.trailofbits.com/)**
  - **這篇說什麼**：Trail of Bits 做密碼學 audit 時反覆看到的錯誤模式
  - **讀哪裡**：整篇
  - **為什麼值得讀**：他們看過的自製 crypto 比你多得多，列出的錯誤模式是真實的戰場經驗

- **["Lessons learned from auditing cryptographic code" — NCC Group](https://www.nccgroup.com/)**
  - **這篇說什麼**：NCC Group 審計密碼學實作時的常見發現
  - **讀哪裡**：整篇
  - **為什麼值得讀**：具體的 code pattern——哪些寫法看起來對但有 subtle bug

→ [Ch 2 數論速覽](./02-number-theory.md)
