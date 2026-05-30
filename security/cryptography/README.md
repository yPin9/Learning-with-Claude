# 密碼學學習筆記：從數學基礎到 Mini-TLS 1.3

> 給懂 C 和 Python、想把密碼學從原語到協議完整走一遍的工程師。

從 modular arithmetic 和 entropy 出發，走過對稱密碼、雜湊、公鑰密碼、AEAD、Post-Quantum，到 TLS 1.3 和 Signal Protocol。每個原語講完就示範攻擊（Padding Oracle、Length Extension、RSA 低指數、Logjam、side-channel）。讀完能用 Python 實作每個原語核心邏輯、用 C 理解 constant-time、看懂 NIST PQC 標準，最後組出 Mini-TLS 1.3。

## 為什麼學這個？

- **安全工程的硬底子**：不理解 AEAD 就不知道為什麼 AES-CBC + HMAC 會被 Padding Oracle 打穿
- **攻擊驅動學習**：講完原語馬上教攻擊，理解「怎麼用錯」是理解設計的最短路徑
- **Post-Quantum 是現在式**：NIST 2024 定案 ML-KEM / ML-DSA；Chrome 已在跑 hybrid PQ key exchange

## 先修知識

- **Python**（會寫 function、讀得懂 `bytes` / `int` 轉換）
- **C 語言**（會讀 pointer、知道 memory layout；side-channel 章節用 C）
- **基礎數學**（高中代數；modular arithmetic 和 group theory 從零教）
- **Linux 基礎**（會用 terminal、裝套件、跑 Docker）
- 不需要：密碼學經驗、線性代數（lattice 章節補直覺）

## 本教材不涵蓋什麼

- **完整的數學證明**（不做 reduction proof；Boneh & Shoup 有）
- **密碼貨幣 / 區塊鏈**（不碰 blockchain、ZKP）
- **HSM 操作**（不教 PKCS#11）
- **合規 / 法律**（不教 FIPS 140 認證流程）

## 課程地圖

### Part 0 — 起點
- [Ch 0 環境搭建](./00-environment-setup.md) / [Ch 1 密碼學全貌](./01-cryptography-overview.md)

### Part 1 — 必要數學
- [Ch 2 數論速覽](./02-number-theory.md) / [Ch 3 機率與資訊論速覽](./03-probability-info-theory.md)

### Part 2 — 古典密碼
- [Ch 4 古典密碼：Caesar、Vigenère、頻率分析](./04-classical-ciphers.md)
- [Ch 5 二戰密碼學：Enigma、Bombe、Turing](./05-wwii-cryptography.md)
- [Ch 6 Shannon 與 OTP：完美保密](./06-shannon-otp.md)

### Part 3 — 對稱密碼
- [Ch 7 區塊密碼基礎：Feistel vs SPN](./07-block-cipher-basics.md)
- [Ch 8 DES / 3DES](./08-des-3des.md)
- [Ch 9 AES 數學：GF(2⁸)](./09-aes-math-gf256.md)
- [Ch 10 AES 實作](./10-aes-implementation.md)
- [Ch 11 區塊模式 + Padding Oracle](./11-block-modes-padding-oracle.md)
- [Ch 12 Stream cipher：RC4、ChaCha20](./12-stream-ciphers.md)
- [練習 A：AES + Padding Oracle](./practice-a-aes-padding-oracle.md)

### Part 4 — 雜湊、MAC、KDF
- [Ch 13 Hash 函式基礎](./13-hash-functions.md) / [Ch 14 SHA 家族](./14-sha-family.md) / [Ch 15 Length Extension Attack](./15-length-extension-attack.md)
- [Ch 16 MAC：HMAC、Poly1305](./16-mac-hmac-poly1305.md) / [Ch 17 密碼雜湊與 KDF](./17-password-hashing-kdf.md)
- [練習 B：SHA-256 + Length Extension](./practice-b-sha256-and-length-extension.md)

### Part 5 — 公鑰密碼
- [Ch 18 公鑰與 DH](./18-public-key-and-dh.md) / [Ch 19 RSA](./19-rsa.md) / [Ch 20 RSA 攻擊](./20-rsa-attacks.md)
- [Ch 21 DH 細節 + Logjam](./21-dh-details-logjam.md) / [Ch 22 橢圓曲線數學](./22-elliptic-curves-math.md)
- [Ch 23 ECDSA / EdDSA / X25519](./23-ecdsa-eddsa-x25519.md) / [Ch 24 數位簽章與 PKI](./24-digital-signatures-pki.md)
- [練習 C：RSA + 攻擊](./practice-c-rsa-and-attacks.md)

### Part 6 — AEAD
- [Ch 25 AEAD 概念](./25-aead-concept.md) / [Ch 26 AES-GCM](./26-aes-gcm.md)
- [Ch 27 ChaCha20-Poly1305 + SIV](./27-chacha20-poly1305-siv.md) / [Ch 28 Nonce 與隨機性](./28-nonce-randomness.md)

### Part 7 — Post-Quantum
- [Ch 29 量子威脅](./29-quantum-threat.md)
- [Ch 30 Lattice 基礎](./30-lattice-basics.md)
- [Ch 31 ML-KEM (Kyber)](./31-ml-kem-kyber.md)
- [Ch 32 ML-DSA (Dilithium)](./32-ml-dsa-dilithium.md)
- [Ch 33 SLH-DSA (SPHINCS+)](./33-slh-dsa-sphincs.md)
- [練習 D：Kyber-512](./practice-d-kyber512.md)

### Part 8 — Protocol
- [Ch 34 TLS 1.3](./34-tls-1.3.md) / [Ch 35 Signal Protocol](./35-signal-protocol.md)
- [Ch 36 Noise Framework](./36-noise-framework.md) / [Ch 37 Protocol 出錯精選](./37-protocol-failures.md)

### Part 9 — 攻擊與密碼分析
- [Ch 38 Side-channel](./38-side-channel.md) / [Ch 39 Constant-time](./39-constant-time.md)
- [Ch 40 隨機數失敗史](./40-rng-failures.md) / [Ch 41 密碼分析方法](./41-cryptanalysis-methods.md)
- [Ch 42 收尾](./42-wrap-up.md)

### Final Project
- [Final Project：Mini-TLS 1.3](./final-project-mini-tls.md)

## 學習方式建議

1. **每章的 code 自己打一遍**：密碼學 code 通常很短（幾十行），但每一行都有意義；複製貼上學不到東西
2. **先攻擊再防禦**：課程在講完原語後馬上教攻擊——理解「怎麼用錯」是最短路徑
3. **用 SageMath 驗證數學**：看到定理就用具體數字跑一次

## 精選資料庫

### 必讀基礎

- **《Serious Cryptography》2nd ed — Aumasson**
  - 目前最好的現代密碼學入門；Ch 1–3 對應 Part 0–1，Ch 4–5 對應 Part 3，Ch 9–11 對應 Part 5，Ch 14 對應 Part 8；第二版加了 PQC

- **《A Graduate Course in Applied Cryptography》— Boneh & Shoup**
  - 學術級教材，免費 PDF（crypto.stanford.edu/~dabo/cryptobook/）；Ch 2–3 建立嚴格安全定義，Ch 8–10 對應 Ch 2，Ch 15–16 對應 Ch 22–23；想做 reduction proof 看這本

### 推薦論文

- **Shannon 1949** "Communication Theory of Secrecy Systems" — 定義 perfect secrecy；對應 Ch 3, Ch 6
- **Diffie & Hellman 1976** "New Directions in Cryptography" — 公鑰起點；對應 Ch 18
- **RSA 1978** "A Method for Obtaining Digital Signatures..." — RSA 原始論文；對應 Ch 19
- **CRYSTALS-Kyber / Dilithium NIST Final Report 2024** — PQC 標準規格；對應 Ch 31–32

### 推薦部落格 / 練習平台

- **[CryptoHack](https://cryptohack.org/)** — 互動式挑戰，每個 Part 學完打對應題目
- **[Cryptopals](https://cryptopals.com/)** — 經典攻擊練習（8 sets）；Part 3–5 對應 Set 1–5
- **[Trail of Bits Blog](https://blog.trailofbits.com/)** — crypto engineering 實戰
- **[Matthew Green's blog](https://blog.cryptographyengineering.com/)** — TLS、Signal 深度分析

### 讀完本課之後

- **《Introduction to Modern Cryptography》3rd ed — Katz & Lindell**（走學術路線的下一本）
- **《Real-World Cryptography》— David Wong**（補 MPC、ZKP、threshold signature）
- **[Project Wycheproof](https://github.com/google/wycheproof)**（Google 的 edge-case 測試集）
