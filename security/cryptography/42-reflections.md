# Ch 42 — 收尾：寫 crypto code 的 do/don't、職涯

> 目標：總結整門課。寫 production crypto code 的 do/don't（don't roll your own、用 audited library、agility 設計）、密碼工程師（OpenSSL maintainer、Cloudflare crypto team）vs 密碼學者（學術、ePrint）兩條路、未來方向（PQC migration、ZK、MPC）。

## 整門課回顧

```
Part 0-1: 基礎 + 數學
Part 2:   古典密碼（baseline 直覺）
Part 3:   對稱密碼（AES、stream cipher、padding oracle）
Part 4:   Hash, MAC, KDF
Part 5:   公鑰密碼（RSA, DH, ECC, PKI）
Part 6:   AEAD 整合
Part 7:   Post-quantum
Part 8:   Protocol 層 (TLS, Signal, Noise)
Part 9:   攻擊與密碼分析

橫貫主題：
  - 數學 → 演算法 → 實作 → protocol → attack
  - 每個演算法配對應的攻擊與 mitigation
  - 學科 vs 工程交織
```

學完你應該能：

- 看 OpenSSL `crypto/aes/aes_core.c` / `crypto/rsa/rsa_ossl.c` 不會懵
- 看 RFC 8446 (TLS 1.3) 知道每個 message
- 看 CVE 知道機制（Heartbleed / ROBOT / Logjam / etc）
- 在 CTF 抓 padding oracle、length extension、weak RSA
- 寫 Application 用 libsodium / cryptography library 不踩雷
- 對 PQC 遷移有 plan 視野

## 寫 Production Crypto Code 的 DO

```
1. 用 audited high-level library:
   - libsodium (簡單 API、安全 default)
   - ring (Rust)
   - Tink (Google, multi-language)
   - Python cryptography
   不要：自己 implement 演算法

2. 用 standard primitive:
   - 對稱：AES-GCM 或 ChaCha20-Poly1305
   - 公鑰：X25519 + Ed25519
   - hash：SHA-256 或 SHA-3
   - KDF：HKDF（high-entropy）, Argon2id（password）
   不要：MD5, SHA-1, RC4, DES, RSA-PKCS1v1.5

3. Const-time:
   - MAC verify 用 const-time compare
   - 密碼學 path 沒 secret-dependent branch

4. 隨機性:
   - 用 OS CSPRNG（getrandom() / os.urandom）
   - 不要 srand() / Math.random() / Mersenne Twister
   - Fork / VM clone 後 reseed

5. Forward secrecy:
   - 用 ephemeral key（ECDHE，不要 static DH）
   - TLS 1.3 預設 forward secrecy

6. 證書 / 簽章驗證:
   - 強制 cert chain 驗
   - 不要 disable cert verification（連 dev 都不該）
   - 用 SAN（Subject Alt Name），不只 CN

7. Key management:
   - Production secret 進 KMS / HSM / vault
   - 不要寫死 key 在 source / config
   - Rotation 政策（每 90 天、每年）

8. Crypto agility:
   - 設計能換演算法（為 PQC 遷移）
   - 不要 hardcoded "用 RSA-2048"
   - 結構：cipher_id || ciphertext

9. Defense in depth:
   - HTTPS + 應用層加密
   - 多層防禦，不依賴單點

10. 監控 / 審計:
    - 記錄密碼操作（不記 secret）
    - 監控 abnormal pattern（多次 failed auth）
    - 定期 security review
```

## 寫 Production Crypto Code 的 DON'T

```
1. 自己 implement 標準演算法（除學習）
2. 用 deprecated 演算法（MD5, SHA-1, RC4, 3DES, ECB mode）
3. 同 (key, nonce) 重用
4. memcmp 比 MAC
5. 寫死 cert / key in source code
6. PII 用 SHA-256 hash 防範識別（hash 不是匿名 — rainbow table）
7. 過度信任 client：加密 cookie 仍要 server 端 MAC + 驗 expiry
8. 把所有東西 encrypt with one key（key separation: encrypt key ≠ MAC key）
9. 用 hashing 取代 encryption（hash 不可逆，但別人也驗不到 plaintext）
10. 把 secret 用 base64 / hex encode 後就以為「保護」
```

## 密碼學職涯路徑

```
                 Cryptographer (學者)
                    ↓
                學術 / 研究院
                    ↓
                IACR / academia
                paper @ CRYPTO, EUROCRYPT
                ePrint server
                
─────────────────────────────────────
                    
              Cryptography Engineer (工程師)
                    ↓
              工業界 / Library / 應用
                    ↓
              OpenSSL maintainer
              Cloudflare crypto team
              Google Tink team
              libsodium、Signal 工程
              FIDO / Webauthn / Passkeys
              Web3 / blockchain crypto
```

### 學者路線

需要：

- PhD（北美 / EU 良好 program）
- 強 math 背景
- 寫 paper、過 IACR 審查、conference talk
- 通常 academic faculty / research lab（IBM Research, Microsoft Research, NTT 等）

收入：學術中等，industry research lab 高。**論文比賽**，不直接 production code。

代表人物：Dan Boneh (Stanford)、Hugo Krawczyk (Algorand Foundation)、Dan Bernstein (CR.YP.TO)、Tanja Lange (TU Eindhoven)、Joan Daemen (AES 設計者)。

### 工程師路線

需要：

- 強 software engineering（C / Rust / Go）
- 對密碼學算法熟（這門課的內容）
- 對 attack 熟（CTF、bug bounty）
- 經驗：security team、open-source contribution

收入：senior security engineer 在大廠（Google / Cloudflare / Apple）很高，比 cryptographer 高。

代表：Adam Langley (BoringSSL, formerly Google)、Filippo Valsorda (formerly Go crypto, now Geomys)、Jean-Philippe Aumasson (Taurus, BLAKE3)。

### Hybrid 路線

industry research lab：兩者都做。Microsoft Research、IBM、Google Research、NTT。

例：Hugo Krawczyk（HKDF 設計者）一直是 industry research（Currently Algorand Foundation）。

## 未來方向

```
Post-quantum migration:
  2025-2035: TLS, SSH, code signing, PKI 全面 PQC
  Hybrid 為主，逐步 PQC-only
  巨大 demand for PQC engineering

Zero-knowledge proofs:
  zk-SNARK, zk-STARK, Bulletproof
  blockchain (Zcash, Ethereum L2) + privacy applications
  
Multi-party computation (MPC):
  threshold signatures (Frost, etc.)
  privacy-preserving ML
  Cosmian、Inpher 等公司

Fully Homomorphic Encryption (FHE):
  運算 over encrypted data
  Microsoft SEAL, OpenFHE, Zama
  仍實用性受限但快速進步

Confidential computing:
  Intel SGX, AMD SEV-SNP, ARM CCA
  hardware-enforced isolation
  AWS Nitro Enclaves, Google Confidential VMs

PKI 革新:
  Certificate Transparency 2.0
  short-lived cert (90 day → 7 day)
  ACME 自動化普及

Web Identity:
  Passkeys (WebAuthn / FIDO2)
  decentralized identity (DID, Verifiable Credentials)
  
Tokenization / Confidential AI:
  PII 加密 + computation
  privacy-preserving inference
```

## CryptoHack / Cryptopals / CTF

繼續學習的好地方：

```
CryptoHack (cryptohack.org):
  互動 challenge 平台
  從基礎到高階
  社群活躍

Cryptopals (cryptopals.com):
  48 道經典題
  從 Set 1 to Set 8
  從 XOR 到 lattice attacks 都有
  做完你已經是 mid-level cryptography engineer

CTF crypto challenges:
  PicoCTF（基礎）
  HackTheBox（中階）
  DEF CON CTF（極高階）
  
ZeroDay Initiative bug bounty:
  專注 cryptography library bug
```

## 推薦持續閱讀

```
Newsletter:
  Cryptography Dispatches (Filippo Valsorda)
  Real World Cryptography Newsletter
  
Blog:
  cryptologie.net (David Wong)
  Trail of Bits blog
  Cloudflare blog (cryptography section)
  Schneier on Security
  
Twitter / X:
  @matthew_d_green
  @TomVanGoethem
  @dchest (Dmitry Chestnykh)
  @bcrypto

Conference:
  CRYPTO (8月, US)
  EUROCRYPT (5月, EU)
  Real World Crypto (1月, RWC)
  CCS, USENIX Security
```

## 工具與 library 收藏

```
Production:
  libsodium (https://libsodium.gitbook.io/)
  ring (Rust)
  BoringSSL (Google fork of OpenSSL)
  Tink (Google, multi-language)
  
Education / experimentation:
  pycryptodome (Python)
  cryptography.io (Python, hazmat layer for low-level)
  PyNaCl (libsodium Python binding)
  rust-crypto crates

PQC:
  liboqs (Open Quantum Safe)
  pq-crystals (Kyber + Dilithium reference)

Math:
  SageMath
  PARI/GP (number theory)
  Sympy (Python)
  
Testing:
  dudect (timing leak detector)
  ctgrind (constant-time analysis)
  Wycheproof (Google test vectors)
  Cryptol (formal verification)
```

## 寫給未來的自己

整門 42 章看完，你應該能：

1. **看新聞不會被嚇**：「Heartbleed 影響我嗎？」「ROBOT 怎麼回事？」「量子電腦會破我密碼嗎？」 — 你都答得出
2. **讀 paper 不會懵**：lattice、ZK、MPC 的入門 paper 你能讀
3. **CTF crypto 題不會繞遠**：看到 weak RSA、padding oracle、length extension 你能秒切入
4. **為公司選 crypto 不會錯**：誰在乎 PQC、TLS 設定、key rotation policy，你都能 advise
5. **看到自己 5 年前的 code 會臉紅**：「啊我寫了 H(secret || message) 當 MAC...」「我用 SHA-256 存密碼...」 — 那是進步

**密碼學是個既古老又快速進化的領域**。AES 23 年沒被破，PQC 正在進入 production。學完這門課不是終點，是進入這個領域的入場票。

接下來想往哪裡走？

- **想做 zk / blockchain**：Zcash protocol spec、Ethereum yellow paper、PLONK paper
- **想做 protocol research**：TLS 1.3 spec 讀完、Noise spec、Signal X3DH paper
- **想做 PQC**：FIPS 203/204/205 spec、liboqs source code
- **想做 cryptanalysis**：Boneh-Shoup textbook、IACR ePrint 找 paper
- **想 production 做 crypto engineer**：投 Cloudflare、Google、Apple、Signal 的 security position

這 42 章是基礎，**從這裡長出去都能站得住**。

— 課程結束 —

→ [Final Project：手刻 Mini-TLS 1.3](./final-project-mini-tls.md)
