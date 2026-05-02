# 密碼學學習筆記：從 GF(2⁸) 到 mini-TLS，含攻擊與 post-quantum

> 給已經會 C / Python、想徹底搞懂密碼學從數學底層到 protocol 整合、能看懂 CVE 也能寫 attack 的工程師。

這是一系列循序漸進的教學文章，**從零教必要數學開始**，一路寫到 AES、RSA、橢圓曲線、AEAD、後量子（ML-KEM / ML-DSA / SLH-DSA）、TLS 1.3 / Signal protocol，並穿插歷史攻擊（Bleichenbacher、Heartbleed、Sony PS3、Debian OpenSSL 等）。每個演算法都「概念 + 手刻 + 攻擊」三位一體。最終你會手刻一個能跟真實 OpenSSL server 互通的 mini-TLS 1.3。

## 為什麼學這個？

- **每天都在用，但 95% 工程師不懂**：HTTPS、SSH、加密貨幣、簽章 — 你用整天，但被問「為什麼選 P-256」「nonce reuse 為什麼是死罪」「Shor 會不會殺光 RSA」答得出嗎？
- **CVE 與安全事件繞不開**：Heartbleed、Logjam、ROBOT、KRACK、PS3 ECDSA、Debian OpenSSL — 每一個都是密碼學課，事後讀新聞學不深，事前學了再讀新聞才有 click。
- **是 attack & defense 工程師的核心**：滲透、CTF、漏洞研究都會碰到 padding oracle、weak RNG、length extension、Bleichenbacher。**懂機制才能 exploit，也才能修**。
- **Post-quantum 已經在敲門**：NIST 2024 標準化了 ML-KEM/ML-DSA/SLH-DSA。10 年內所有公鑰系統要 migrate。**現在學，剛好趕上**。
- **底層數學其實沒那麼可怕**：一般密碼學課躲了數學，這門課從零教 modular arithmetic、群環體、有限體、橢圓曲線群、lattice — 用得到才教，用不到的數學系深奧概念跳過。

## 課程地圖

### Part 0 — 起點
- [Ch 0 環境搭建：Python cryptography、OpenSSL/mbedTLS、SageMath、pwntools](./00-environment-setup.md)
- [Ch 1 密碼學全貌：學科 vs 工程，為什麼自己 roll crypto 是壞主意](./01-cryptography-overview.md)

### Part 1 — 必要數學（速覽，後續邊用邊補）
- [Ch 2 數論速覽：modular、ext-Euclid、CRT、群/環/體直覺](./02-number-theory.md)
- [Ch 3 機率與資訊論速覽：entropy、Shannon、PRG、IND-CPA 直覺](./03-probability-info-theory.md)

### Part 2 — 古典密碼（故事與 baseline）
- [Ch 4 古典密碼：Caesar、Vigenère、頻率分析破譯](./04-classical-ciphers.md)
- [Ch 5 二戰密碼學：Enigma、Bombe、Turing 與 Bletchley Park](./05-wwii-cryptography.md)
- [Ch 6 Shannon 與一次性密碼本：完美保密證明，為什麼 OTP 工程上沒用](./06-shannon-otp.md)

### Part 3 — 對稱密碼
- [Ch 7 區塊密碼基礎：Feistel vs SPN、IND-CPA 嚴格定義](./07-block-cipher-basics.md)
- [Ch 8 DES / 3DES：歷史、NSA 改 S-box 的故事、為什麼被淘汰](./08-des-3des.md)
- [Ch 9 AES 數學：GF(2⁸) 從零、SubBytes / MixColumns 推導](./09-aes-math.md)
- [Ch 10 AES 完整實作：Rijndael 全流程、AES-NI 硬體加速](./10-aes-implementation.md)
- [Ch 11 區塊模式 + padding oracle：ECB / CBC / CTR + Vaudenay 攻擊](./11-block-modes-padding-oracle.md)
- [Ch 12 Stream cipher：RC4 興衰、ChaCha20 為什麼贏](./12-stream-ciphers.md)
- [練習 A：手刻 AES-128（C + Python）+ 跑 padding oracle 解密](./practice-a-aes-and-padding-oracle.md)

### Part 4 — 雜湊、MAC、KDF
- [Ch 13 Hash 函式：抗碰撞 / 原像 / 第二原像、Merkle-Damgård](./13-hash-functions.md)
- [Ch 14 SHA 家族：SHA-1 collision、SHA-2、SHA-3 sponge 為什麼贏](./14-sha-family.md)
- [Ch 15 Length extension attack：Merkle-Damgård 的天生缺陷](./15-length-extension-attack.md)
- [Ch 16 MAC：HMAC、GMAC、Poly1305 的 Wegman-Carter 構造](./16-mac-hmac-poly1305.md)
- [Ch 17 密碼雜湊與 KDF：PBKDF2、bcrypt、scrypt、Argon2](./17-password-hashing-kdf.md)
- [練習 B：實作 SHA-256 + HMAC + 對自寫 SHA-1 跑 length extension 打爆它](./practice-b-sha256-and-length-extension.md)

### Part 5 — 公鑰密碼
- [Ch 18 公鑰動機與 Diffie-Hellman：1976 的革命](./18-public-key-and-dh.md)
- [Ch 19 RSA：Euler totient、CRT 加速、padding 模式](./19-rsa.md)
- [Ch 20 RSA 攻擊：Wiener、common modulus、Hastad、Bleichenbacher 1998](./20-rsa-attacks.md)
- [Ch 21 Diffie-Hellman 細節：DLP、small subgroup、Logjam](./21-dh-details-logjam.md)
- [Ch 22 橢圓曲線數學：群運算、Montgomery ladder、Curve25519 設計](./22-elliptic-curves-math.md)
- [Ch 23 ECDSA / EdDSA / X25519：簽章與 ECDH 實務](./23-ecdsa-eddsa-x25519.md)
- [Ch 24 數位簽章與 PKI：X.509、CA chain、Let's Encrypt](./24-digital-signatures-pki.md)
- [練習 C：手刻 RSA-2048 + 跑三個經典攻擊（Wiener / Hastad / Bleichenbacher）](./practice-c-rsa-and-attacks.md)

### Part 6 — AEAD 與整合
- [Ch 25 AEAD 概念：encrypt-then-MAC、IND-CCA2、為什麼 unauth encryption 已死](./25-aead-concepts.md)
- [Ch 26 AES-GCM 解剖：GHASH、nonce 結構、misuse 災難](./26-aes-gcm.md)
- [Ch 27 ChaCha20-Poly1305 與 AES-GCM-SIV：nonce-misuse-resistance](./27-chacha20-poly1305-siv.md)
- [Ch 28 Nonce 與隨機性正確使用：Sony PS3、Debian OpenSSL CVE-2008-0166](./28-nonce-and-randomness.md)

### Part 7 — Post-Quantum（認真做）
- [Ch 29 量子威脅：Shor 為什麼幹掉 RSA/ECC、Grover、harvest now decrypt later](./29-quantum-threat.md)
- [Ch 30 Lattice 基礎：LWE、Module-LWE、SVP / CVP，為什麼 NIST 選格密碼](./30-lattice-basics.md)
- [Ch 31 ML-KEM (Kyber) 解剖：encapsulation / decapsulation 全流程](./31-ml-kem-kyber.md)
- [Ch 32 ML-DSA (Dilithium)：lattice 簽章、Fiat-Shamir 變形](./32-ml-dsa-dilithium.md)
- [Ch 33 SLH-DSA / SPHINCS+：hash-based 簽章為什麼適合 long-term assurance](./33-slh-dsa-sphincs.md)
- [練習 D：簡化版 Kyber-512 KEM 實作（Python）](./practice-d-kyber-512.md)

### Part 8 — Protocol 層
- [Ch 34 TLS 1.3 握手：1-RTT、0-RTT、HKDF schedule、為什麼砍掉 TLS 1.2](./34-tls-1-3.md)
- [Ch 35 Signal Protocol：X3DH 起手 + Double Ratchet](./35-signal-protocol.md)
- [Ch 36 Noise Framework：handshake pattern 系統化（IK / XX / NK）](./36-noise-framework.md)
- [Ch 37 Protocol 出錯精選：Heartbleed、ROBOT、Logjam、TLS Triple Handshake](./37-protocol-failures.md)

### Part 9 — 攻擊與密碼分析
- [Ch 38 Side-channel：timing、power、EM、cache（FLUSH+RELOAD / PRIME+PROBE）](./38-side-channel.md)
- [Ch 39 Constant-time programming：寫 const-time AES、ECC 的紀律](./39-constant-time.md)
- [Ch 40 隨機數失敗史：Debian OpenSSL、PS3、Dual_EC_DRBG 後門故事](./40-randomness-failures.md)
- [Ch 41 密碼分析方法：differential、linear、algebraic、meet-in-the-middle](./41-cryptanalysis.md)
- [Ch 42 收尾：寫 crypto code 的 do/don't、密碼工程師 vs 密碼學者職涯](./42-reflections.md)

### Final Project
- [Final Project：手刻 Mini-TLS 1.3，與真實 OpenSSL server 互通](./final-project-mini-tls.md)

## 學習方式建議

1. **每章親手敲過**：密碼學 bug 都在細節（一個 byte 順序錯、一個 mod 不對都會崩）。看不懂的地方一定 print 中間值對照。
2. **故意做錯**：把 nonce 重複用、把 padding 寫錯、把 RSA exponent 設 3 — 看自己怎麼被打爆。這套教材會反覆主動帶你做這些事。
3. **數學別跳**：Part 1 的 Ch 2 / Ch 3 兩章是基礎。覺得太簡單可以快過，但別跳 — 後面 ECC、lattice、Shannon entropy 都從這裡長出來。
4. **C 版 + Python 版兩邊都看**：Python 寫得清晰但不安全（沒 const-time）；C 版貼近 OpenSSL 真實寫法。**懂兩者差異 = 看懂 production crypto code**。
5. **去 CryptoHack 練手**：<https://cryptohack.org/> 是社群最好的 crypto CTF 平台，本教材講過的概念都能在那找對應題。
6. **不要寫 production 用自己刻的密碼**：學習用 OK，上線一定用 audited library（libsodium、ring、Tink）。Ch 1 會展開為什麼。

## 本教材不涵蓋什麼

- **不教 zero-knowledge proofs / SNARKs / STARKs**：這是另一個大坑（zk 課可以另開）。本課只到 Schnorr 簽章程度的 proof of knowledge。
- **不教 homomorphic encryption**：FHE 自成一個世界，本課只在 PQ 章提到 lattice 與 FHE 的關係。
- **不深入 multi-party computation**：MPC 是專門領域，本課只在 protocol 章提到 secret sharing。
- **不教 blockchain crypto**：以太坊 / 比特幣的密碼學技術是工程組合（ECDSA + SHA-256 + Merkle tree），本課提供基礎，但不專做加密貨幣案例。
- **不教密碼史完整版**：除了二戰章，更早（凱撒、阿拉伯黃金時代、文藝復興）只在 Ch 4 簡略帶。完整密碼史看 Simon Singh 的書。

## 參考資料

**書（推薦順序）：**
- 《Serious Cryptography》— Jean-Philippe Aumasson（最好的現代入門，2024 第 2 版）
- 《Real-World Cryptography》— David Wong（協定與工程實務）
- 《Cryptography Engineering》— Schneier / Ferguson / Kohno（經典，2010）
- 《Handbook of Applied Cryptography》— Menezes 等（reference 級，免費 PDF）
- 《A Graduate Course in Applied Cryptography》— Boneh & Shoup（學術深度，免費）

**線上資源：**
- CryptoHack：<https://cryptohack.org/>（互動 challenge 平台）
- Cryptopals：<https://cryptopals.com/>（48 道經典 crypto 題）
- Dan Boneh's Coursera Cryptography I：<https://www.coursera.org/learn/crypto>

**標準與規範：**
- NIST FIPS / SP 系列：AES (FIPS 197)、SHA (FIPS 180-4)、ML-KEM (FIPS 203)、ML-DSA (FIPS 204)、SLH-DSA (FIPS 205)
- IETF RFC：TLS 1.3 (RFC 8446)、ChaCha20-Poly1305 (RFC 8439)、Curve25519 (RFC 7748)、HKDF (RFC 5869)
- Noise Framework：<https://noiseprotocol.org/noise.html>

**工具：**
- Python `cryptography`：<https://cryptography.io/>
- libsodium：<https://libsodium.gitbook.io/>
- OpenSSL / mbedTLS / BoringSSL
- SageMath：<https://www.sagemath.org/>（密碼學數學驗算）
- pwntools：<https://docs.pwntools.com/>（攻擊用）
