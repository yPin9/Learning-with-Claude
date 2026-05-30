# Ch 42 — 收尾：密碼工程的 Do/Don't 與未來方向

> **目標**：總結 43 章的密碼工程 do/don't，展望密碼學者 vs 密碼工程師的職涯差異，知道接下來去哪裡繼續學。

---

## 為什麼需要這個？

你花了 41 章從 modular arithmetic 走到 side-channel。現在把所有東西裝進一個 decision framework：遇到真實的密碼工程問題時，你知道該做什麼、不該做什麼、不確定時去查什麼。

這章沒有新技術，但它可能是你最常回來翻的一章。

---

## 先建立直覺

```
密碼學的工程現實：

  學術界：「我們證明了這個 scheme 在 random oracle model 下是 IND-CCA2 secure」
  工程界：「我 deploy 了這個 scheme，然後因為 nonce reuse 被打穿了」

  gap 在哪？

  學術界的 proof 假設：
    ✓ 演算法正確實作
    ✓ 隨機數是真隨機
    ✓ 沒有 side-channel
    ✓ key 被安全管理
    ✓ protocol 的每一步都按規格執行

  工程界的現實：
    ✗ OpenSSL 的 Heartbleed（buffer over-read）
    ✗ Debian 的 entropy 被砍（RNG failure）
    ✗ AES T-table 的 cache side-channel
    ✗ AWS S3 的 key 被 commit 到 GitHub
    ✗ GnuTLS 的 goto fail（protocol step skipped）

  密碼工程師的工作 = 填這個 gap
```

---

## 核心概念：The Do List

### Do 1：用經過審計的 library

| Library | 語言 | 特點 |
|---|---|---|
| libsodium (NaCl) | C | 高階 API、hard to misuse、constant-time |
| ring | Rust | BoringSSL Rust port、memory-safe |
| Tink | 多語言 | Google、key management 內建、misuse-resistant |
| OpenSSL 3.x | C | 功能最全，但 API 複雜容易用錯 |
| BoringSSL | C | Google fork、精簡 + hardened |
| pyca/cryptography | Python | 底層 OpenSSL、高階 Fernet + 低階 hazmat |

選擇原則：被第三方 audit？constant-time 保證？misuse-resistant API？維護活躍？

### Do 2：做 Threat Modeling

在寫任何 crypto code 之前，先回答這些問題：

```
Threat Model Checklist：

□ 你在保護什麼？（data at rest / data in transit / 身份驗證）
□ 對手是誰？（腳本小子 / 犯罪組織 / 國家級攻擊者）
□ 對手有什麼 access？（network / co-located VM / physical access）
□ 你能接受什麼 failure？（短暫中斷 vs 機密洩露）

根據答案選 primitive：

  Data in transit + 雙向認證？     → TLS 1.3
  Data at rest + 靜態加密？        → AES-256-GCM + KMS
  密碼驗證？                       → Argon2id
  數位簽章？                       → Ed25519 或 ECDSA P-256
  Key exchange？                   → X25519 或 ECDHE P-256
  需要 Post-Quantum？              → ML-KEM-768 + X25519 hybrid
```

### Do 3：Follow 標準

```
標準的價值：

  NIST SP 800-系列   → 美國政府的密碼學指引（key management, RNG, algorithm selection）
  RFC 系列           → 網路協議的規格（TLS 1.3 = RFC 8446, X25519 = RFC 7748）
  FIPS 140-3         → 密碼學模組的安全認證
  Common Criteria    → 更廣泛的安全認證（包含 side-channel evaluation）

  不是說標準永遠正確（Dual_EC_DRBG 也是 NIST 標準）
  但標準提供 baseline——偏離標準的決定需要更強的 justification
```

### Do 4：Constant-time implementation

Ch 38-39 教了技術細節。原則：

```
  任何處理 secret data 的 code：
    ✓ 沒有 secret-dependent branch
    ✓ 沒有 secret-dependent memory access
    ✓ 沒有 secret-dependent loop count
    ✓ 用 compiler barrier 阻止優化
    ✓ 或者直接用 libsodium/BearSSL 這些已驗證的 library
```

### Do 5：Key rotation 和 key lifecycle

```
Key lifecycle：

  Generation → Storage → Usage → Rotation → Destruction

  Generation：用 CSPRNG（Ch 40），在高 entropy 的環境中
  Storage：HSM / KMS / 至少 encrypted at rest
  Usage：限制用途（一把 key 只做一件事）
  Rotation：定期更換（TLS session key 每次 handshake 換一把）
  Destruction：確保 key material 被 zeroize（不能靠 GC / free）

  ┌──────────────────────────────────────────────────┐
  │ Key zeroization 的陷阱：                          │
  │                                                  │
  │ memset(key_buffer, 0, 32);   // compiler 可能    │
  │                               // 優化掉這行！    │
  │                               // （dead store     │
  │                               //  elimination）   │
  │                                                  │
  │ 正確做法：                                       │
  │   explicit_bzero(key_buffer, 32);  // POSIX      │
  │   SecureZeroMemory(key_buffer, 32); // Windows   │
  │   sodium_memzero(key_buffer, 32);  // libsodium  │
  └──────────────────────────────────────────────────┘
```

---

## 底層機制：The Don't List

### Don't 1：自己設計密碼演算法

```
你覺得你的 cipher 安全，因為你想不到怎麼破它。
這叫 security through obscurity。

密碼學者能想到的攻擊方法遠超你的想像：
  - Differential / linear cryptanalysis（Ch 41）
  - Algebraic attack
  - Related-key attack
  - Slide attack
  - Boomerang attack
  - Invariant subspace attack
  - ...

AES 的設計團隊是兩位頂尖密碼學者 + NIST 組織的全球公開評選 + 5 年的學術審查。
你不可能在業餘時間設計出比 AES 更安全的東西。
```

### Don't 2：自己實作 production crypto

```
「但我想學習所以自己寫」→ 學習用的 implementation 不要上生產

  學習用：本課的所有 Python 範例，教育目的，永遠不要 deploy
  生產用：libsodium、ring、OpenSSL、BoringSSL、Tink

  理由：
  1. Constant-time 很難做對（Ch 39）
  2. Memory management 會出錯（Heartbleed = 一個 memcpy 少了 bounds check）
  3. Error handling 會洩漏資訊（padding oracle = error message 太詳細）
  4. Edge case 無窮無盡（Project Wycheproof 有 80,000+ test vectors）
```

### Don't 3：ECB mode

```
Ch 11 已經展示過 ECB 的問題：

  ECB 加密後的 penguin 圖片 → 企鵝的輪廓清晰可見
  → 相同的 plaintext block = 相同的 ciphertext block
  → 任何有 pattern 的資料都會洩漏 pattern

  唯一合法的 ECB 用法：加密一個 single block（例如 key wrap）
  除此之外，永遠用 CTR、GCM、或其他 mode
```

### Don't 4：MD5 / SHA-1

```
MD5（1992）：
  2004 年 Wang et al. 找到 collision → 2^20 次計算
  2008 年 Stevens 用 MD5 collision 偽造 X.509 證書
  → 任何依賴 collision resistance 的場景都不能用 MD5

SHA-1（1995）：
  2017 年 Google 的 SHAttered 找到 collision（兩個不同的 PDF 有相同 hash）
  → 花了約 2^63 次計算（110 GPU-years）
  → SHA-1 collision 是 practical break

SHA-256/SHA-3：目前安全，應用於所有新系統
```

### Don't 5：Hardcode key

```python
# ✗ 永遠不要
AES_KEY = b"mysecretkey12345"  # hardcoded in source code

# 更不要
AES_KEY = b"mysecretkey12345"  # committed to Git

# ✓ 正確做法
import os
AES_KEY = os.environ.get("AES_KEY")  # 從環境變數讀取
# 或從 KMS / Vault / HSM 取得
```

GitHub 的 secret scanning 每天掃描數十萬次 commit，找到的 hardcoded credentials 數量驚人。

### Don't 6：Reuse nonce

```
Ch 28 已經詳細講過。
AES-GCM nonce reuse → 洩漏 authentication key → forgery
AES-CTR nonce reuse → plaintext XOR 洩漏
ECDSA nonce reuse → private key 洩漏

防禦：
  1. 用 counter-based nonce（如果能保證不 reset）
  2. 用 random nonce（如果 nonce space 夠大——AES-GCM 的 96-bit nonce 不夠！）
  3. 用 SIV mode（nonce-misuse resistant）
  4. 用 XChaCha20-Poly1305（192-bit nonce → random nonce 安全）
```

---

## 進一步用法：密碼學者 vs 密碼工程師

### 兩條路

```
密碼學者（Cryptographer）：
  ├── 工作內容：設計新演算法、證明安全性、發表論文
  ├── 背景需求：數學博士（數論、代數幾何、格理論）
  ├── 工具：SageMath、LaTeX、Magma
  ├── 產出：paper、proof、new primitive
  ├── 去哪：大學研究職位、IACR 會議（CRYPTO、EUROCRYPT、ASIACRYPT）
  └── 例子：Dan Boneh、Shafi Goldwasser、Yael Kalai

密碼工程師（Cryptographic Engineer）：
  ├── 工作內容：把密碼學原語安全地嵌入系統
  ├── 背景需求：CS 學士/碩士 + 密碼學知識 + 系統/安全經驗
  ├── 工具：C、Rust、Python、OpenSSL、Wireshark、gdb
  ├── 產出：secure implementation、protocol design、security audit
  ├── 去哪：科技公司、資安公司、政府機構
  └── 例子：Thomas Pornin（BearSSL）、Filippo Valsorda（age, Go crypto）

你剛讀完的這門課 → 密碼工程師的起點
如果要走密碼學者路線 → 需要再讀 Boneh & Shoup 的 reduction proof + 線性代數 + 格理論
```

### 職涯對比

| 面向 | 密碼學者 | 密碼工程師 |
|---|---|---|
| 核心技能 | 數學證明、安全歸約 | 實作、系統安全、code review |
| 典型雇主 | 大學、研究院、大公司研究部門 | 科技公司、資安顧問、政府 |
| 產出形式 | 論文、標準提案 | 安全的 code、audit report |
| 日常 | 證明定理、跑 SageMath | 寫 C/Rust、review PR、抓 side-channel |
| 入門門檻 | PhD 通常必要 | CS 學位 + 密碼學自學 |
| 薪資範圍 | 學術偏低，業界研究員高 | 資安市場行情，senior 很高 |

### 兩者的交集

在 protocol design 這個領域，兩種角色需要合作：

- 密碼學者設計 protocol 的數學框架（例如 Signal Protocol 的 Double Ratchet）
- 密碼工程師把 protocol 實作成安全的 code（例如 libsignal）
- 安全研究員用 formal verification 檢驗兩者的一致性（例如 ProVerif、Tamarin）

---

## 對比與取捨

### 常見密碼學決策 cheatsheet

| 場景 | 推薦 | 不要用 | 理由 |
|---|---|---|---|
| 對稱加密 | AES-256-GCM, ChaCha20-Poly1305 | AES-ECB, AES-CBC（無 MAC） | AEAD 防止 tampering |
| Hashing | SHA-256, SHA-3, BLAKE2b | MD5, SHA-1 | collision broken |
| 密碼 hashing | Argon2id | MD5, SHA-256（raw） | memory-hard 防 GPU |
| MAC | HMAC-SHA256, Poly1305（搭配 AEAD） | 自製 MAC | 理論保證 |
| Key exchange | X25519, ECDHE-P256 | RSA key transport, DH-1024 | forward secrecy |
| 數位簽章 | Ed25519, ECDSA-P256 | RSA-1024, DSA | 安全性/效能 |
| PQC KEM | ML-KEM-768 + X25519 hybrid | 單用 ML-KEM（太新） | hedge both sides |
| PQC signature | ML-DSA-65 | 自選 lattice scheme | NIST 標準化 |
| TLS | TLS 1.3 | TLS 1.0/1.1, SSL 3.0 | 已知攻擊 |
| RNG | os.urandom, secrets, getrandom | random.random, rand() | CSPRNG vs MT |
| Nonce | counter 或 large-random + SIV | 96-bit random（AES-GCM） | birthday bound |

---

## 踩雷集錦

### 雷 1：「我用了 AES-256 所以安全」

AES-256 只保證 confidentiality 的 primitive 是安全的。但你可能：

- 用了 ECB mode → pattern 洩漏
- 沒做 authentication → 被 bit-flip
- nonce reuse → GCM 的 auth key 洩漏
- hardcoded key → key 在 GitHub 上
- key 存在 plaintext 檔案裡 → 被檔案系統存取

**密碼學的安全是系統性的，不是某一層的。**

### 雷 2：「我的 threat model 是 everybody」

沒有系統能抵擋所有攻擊者。如果你的 threat model 是「NSA + physical access + unlimited budget」，你基本上什麼都不能 deploy。

實際做法：根據你的場景定義合理的 threat model，然後在那個 model 下做到最好。一個 web application 不需要抵擋 power analysis（攻擊者沒有物理 access），但需要抵擋 timing attack（攻擊者有 network access）。

### 雷 3：「加密了就安全了」

加密只提供 confidentiality。你可能還需要：

- **Integrity**：資料沒被篡改 → MAC / AEAD
- **Authenticity**：資料來自正確的人 → 數位簽章
- **Freshness**：資料不是 replay → timestamp / nonce
- **Non-repudiation**：發送者不能否認 → 數位簽章（MAC 不提供）
- **Forward secrecy**：過去的通訊在 key 洩漏後仍安全 → ephemeral DH

### 雷 4：遷移成本被低估

從 RSA 遷移到 ECC → 需要更新所有 certificate、client、server。
從 classical 遷移到 PQC → 需要更新所有 TLS stack、VPN、email encryption。
從 TLS 1.2 遷移到 TLS 1.3 → 需要確保所有 client 支援。

**密碼學的技術債是最貴的技術債之一**，因為它牽涉到每一個通訊端點。

### 雷 5：忽略 key management

90% 的密碼學 failure 不是 algorithm failure，而是 key management failure：

- key 被 commit 到 Git
- key 被寫在 config file 裡，沒有加密
- key rotation 沒有做，用了 10 年的同一把 key
- 離職員工的 key 沒有 revoke
- key 備份和主 key 存在同一台機器上

---

## 進階

### Formal Verification of Crypto Protocol

- **ProVerif**：用 applied pi-calculus 建模，自動檢查 secrecy/authentication/forward secrecy（TLS 1.3 用它驗證）
- **Tamarin Prover**：multiset rewriting rules，支援更複雜的 state machine（Signal Protocol 用它驗證）
- **F* / Project Everest**：dependently-typed language，miTLS 的 type system 保證安全性

### Post-Quantum Migration

2024 年 NIST 定案 ML-KEM/ML-DSA/SLH-DSA，Chrome/Firefox 已開始 hybrid PQ key exchange。遷移策略：立刻 audit crypto inventory → 短期 TLS hybrid PQ → 中期簽章遷移 → 長期淘汰 classical。**Harvest Now, Decrypt Later**：對手現在收集你的加密通訊，等量子電腦出來再解密——機密性要求 > 10 年的資料必須現在就用 PQC。

---

## 動手練習

1. **Crypto Audit**：審計一個你寫過的（或你熟悉的）open-source 專案的密碼學用法。用本章的 Do/Don't checklist，列出至少三個做對的地方和三個可以改進的地方。

2. **Key Management 設計**：為一個簡單的 web application（用戶登入 + API token）設計 key management 流程。畫出 key lifecycle 圖（generation → storage → usage → rotation → destruction），標出每一步用什麼工具（KMS? Vault? env var?）。

3. **Threat Model 練習**：為以下場景寫出 threat model：
   - 一個 messaging app（對手：service provider、network attacker、國家級攻擊者）
   - 說明你會用什麼 primitive（key exchange、encryption、authentication）以及為什麼

4. **CryptoHack / Cryptopals 挑戰**：如果你還沒做過，現在去做 [CryptoHack](https://cryptohack.org/) 的 Introduction 和 Symmetric Ciphers 段落。或者做 [Cryptopals](https://cryptopals.com/) Set 1。

---

## 重點整理

```
Do：
  ✓ 用經過審計的 library（libsodium、ring、Tink）
  ✓ 做 threat modeling（保護什麼、對手是誰、什麼 access）
  ✓ Follow NIST/IETF 標準
  ✓ Constant-time implementation
  ✓ Key lifecycle management（生成→儲存→使用→輪替→銷毀）
  ✓ 開始規劃 PQC 遷移

Don't：
  ✗ 自己設計 cipher
  ✗ 自己實作 production crypto
  ✗ 用 ECB mode
  ✗ 用 MD5/SHA-1
  ✗ Hardcode key（特別是 commit 到 Git）
  ✗ Reuse nonce

密碼學者 vs 密碼工程師：
  密碼學者 → 設計演算法、證明安全性、PhD、數學
  密碼工程師 → 安全實作、protocol 整合、code review、系統安全
  本課 → 密碼工程師的起點

接下來：
  CryptoHack / Cryptopals → 實戰練習
  Boneh & Shoup → 學術深度
  Final Project → Mini-TLS 1.3
```

---

## 自我檢核

- [ ] 能列出至少三個「經過審計的 crypto library」並說出各自的優勢
- [ ] 能寫出一個簡單場景的 threat model（保護什麼、對手是誰、用什麼 primitive）
- [ ] 能解釋為什麼「自己設計 cipher」是壞主意（列出至少三種你不知道的攻擊方法）
- [ ] 能解釋 key lifecycle 的五個階段
- [ ] 能解釋 `memset(key, 0, 32)` 為什麼可能被 compiler 優化掉
- [ ] 能區分密碼學者和密碼工程師的工作內容和技能需求
- [ ] 知道 PQC migration 的時間線和 HNDL 威脅
- [ ] 能用 Do/Don't checklist 審計一個真實系統的密碼學用法

---

## 延伸閱讀

- **[CryptoHack](https://cryptohack.org/)**
  - 互動式密碼學挑戰平台——從 XOR 到 RSA 到 ECC，本課每個 Part 學完後去打對應的題目效果最好

- **[Cryptopals](https://cryptopals.com/)**
  - 經典的密碼學攻擊練習集（8 sets, 64 challenges）——本課 Part 3-5 的攻擊章節直接對應 Set 1-5

- **[Project Wycheproof](https://github.com/google/wycheproof)**
  - Google 的密碼學 edge-case 測試集——80,000+ test vectors，用它來測你的 Mini-TLS 1.3

- **《A Graduate Course in Applied Cryptography》— Dan Boneh & Victor Shoup**
  - 免費 PDF：https://crypto.stanford.edu/~dabo/cryptobook/
  - 學術級密碼學教材——想往密碼學者方向走的下一步

- **《Real-World Cryptography》— David Wong**
  - 補充本課沒深入的 MPC、ZKP、threshold signature、secure messaging
  - 適合密碼工程師的進階讀物

---

→ [Final Project — Mini-TLS 1.3](./final-project-mini-tls.md)
