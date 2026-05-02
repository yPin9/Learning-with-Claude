# Ch 23 — ECDSA / EdDSA / X25519：簽章與 ECDH 實務

> 目標：把 EC 群拿來做事 — ECDSA 簽章、EdDSA（Ed25519）為什麼比 ECDSA 更穩（deterministic nonce）、X25519 的 ECDH key agreement、ECDSA 的 nonce reuse 災難（Sony PS3、Bitcoin 早期錢包）。

## ECDH = X25519 / X448

X25519 已 Ch 22 介紹，重點：

- **Curve25519 的 Montgomery ladder ECDH**
- 私鑰 32 byte，公鑰 32 byte，shared 32 byte
- Const-time 天生
- IETF RFC 7748

```python
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

priv = X25519PrivateKey.generate()
pub = priv.public_key()
shared = priv.exchange(other_pub)
```

X448（Curve448）類似但更高安全（224-bit security）— 沒人用，Curve25519 夠。

## ECDSA：Elliptic Curve DSA

NIST FIPS 186-4 標準。**最廣用 EC 簽章** — Bitcoin、TLS 大部分證書、code signing 等。

### 演算法

```
KeyGen:
  d ∈ [1, n-1] 隨機（n = curve order）
  Q = d × G   ← public key

Sign(m, d):
  k ∈ [1, n-1] 隨機 (nonce)
  R = k × G = (x_R, y_R)
  r = x_R mod n
  s = k⁻¹ × (H(m) + r × d) mod n
  return (r, s)

Verify(m, (r, s), Q):
  u_1 = H(m) × s⁻¹ mod n
  u_2 = r × s⁻¹ mod n
  P = u_1 × G + u_2 × Q
  return P.x mod n == r
```

### Nonce reuse 災難

```
若兩個簽章 (r, s_1) 與 (r, s_2) 用同 k：
  s_1 = k⁻¹ (H(m_1) + r × d)
  s_2 = k⁻¹ (H(m_2) + r × d)
  s_1 - s_2 = k⁻¹ (H(m_1) - H(m_2))
  k = (H(m_1) - H(m_2)) / (s_1 - s_2)
  d = (s_1 × k - H(m_1)) / r
  
attacker 直接拿到私鑰 d
```

### 真實案例

**Sony PS3 (2010)**：fail0verflow 在 27c3 demo PS3 firmware 簽章用同 nonce。攻擊者算出 Sony 的私鑰 → 任意簽 firmware → 越獄成功。Sony 後來起訴 GeoHot、Hotz，最終庭外和解。**這是 ECDSA 史上最有名的實作災難**。

**Bitcoin 早期錢包 (2013)**：Android 某個 RNG bug 讓多個簽章重複 k → 私鑰外洩 → 比特幣被盜。直接觸發 deterministic nonce 標準化討論。

### Partial nonce leak

更狠：**只洩漏 k 的幾個 bit** 也能用 lattice attack 還原 d（Howgrave-Graham-Smart 1999）。**timing side-channel 洩漏 k 的 LSB 就足以**。

實務 mitigation：const-time scalar mult（Montgomery ladder）+ **deterministic nonce**。

## RFC 6979：Deterministic ECDSA

```
k = HMAC-DRBG(d, H(m))
```

**從 d 與 m 確定地產生 k**，而非隨機。優點：

- **不需 RNG**（嵌入式設備更安全）
- **同 (m, d) 永遠同 sig**（可重現）
- **沒 nonce reuse 災難**（k 由 m 決定，不同 m → 不同 k）

但仍受 side-channel timing attack（k 是 secret）— 必配 const-time 實作。

GnuPG、Ledger 硬體錢包、libsecp256k1 等都用 RFC 6979。

## EdDSA / Ed25519：Bernstein 的更好簽章

Bernstein 2011 提（curve25519 的 sister）。**設計目標：避開所有 ECDSA 已知坑**：

```
Ed25519 sign(m, sk):
  h = SHA-512(sk)
  s = h[:32]   (private scalar)
  prefix = h[32:64]
  
  r = SHA-512(prefix || m)
  R = r × G
  
  hashed = SHA-512(R || A || m)   where A = s × G (public key)
  S = (r + hashed × s) mod ℓ      where ℓ = group order
  
  return (R, S)

Verify (m, (R, S), A):
  hashed = SHA-512(R || A || m)
  return S × G == R + hashed × A
```

特點：

- **deterministic 內建**（從 hash 算 r，不需 RNG）
- **不需 modular inversion**（ECDSA 的 s = k⁻¹×... 是 timing 痛點）
- **不需檢查 r ≠ 0**
- **小 key + 小 sig**：32 byte sk、32 byte pk、64 byte sig
- **const-time 容易實作**（Curve25519 Montgomery ladder）

**Ed25519 比 ECDSA 在 Curve25519 上**：

- 沒 nonce 洩漏風險
- 沒 modular inversion side-channel
- 工程上更難寫錯

**新系統優先 Ed25519**。SSH、libsodium、Signal、WireGuard、Tor、modern OpenPGP 全用。

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

priv = Ed25519PrivateKey.generate()
pub = priv.public_key()

sig = priv.sign(b"hello world")
pub.verify(sig, b"hello world")
```

## Ed448：更高安全

curve448 上的 EdDSA。224-bit security（vs Ed25519 的 128-bit）。

慢一點、key 大一點。**只在合規需求或極長期 key 場景用**。多數場景 Ed25519 夠。

## ECDSA vs EdDSA 對照

| | ECDSA (P-256) | Ed25519 |
|---|---|---|
| Nonce | 隨機 (or RFC 6979) | deterministic 內建 |
| 私鑰 | 32 byte | 32 byte |
| 公鑰 | 33 byte (compressed) | 32 byte |
| 簽章 | 64-72 byte | 64 byte |
| Modular inversion | 需要（s = k⁻¹×...） | 不需要 |
| Curve | NIST P-256 | Curve25519 (Edwards form) |
| 廣泛採用 | Bitcoin、TLS、Web | SSH、Signal、modern web |
| 推薦 | 合規場景 | **新系統首選** |

## Schnorr：另一種簽章（不在 NIST 標準但好）

Bitcoin Taproot (2021) 引入 **Schnorr 簽章**：

```
Schnorr sign(m, d):
  k 隨機
  R = k × G
  e = H(R || P || m)
  s = k - e × d mod n
  return (R, s)

Verify(m, (R, s), P):
  e = H(R || P || m)
  return R == s × G + e × P
```

**簽章可線性組合**（multi-sig 自然）→ MuSig 等 protocol。Bitcoin 用 Schnorr 取代 ECDSA 為了：

- 更小 multi-sig（n-of-n 可壓成單個簽章）
- 更好 cryptanalysis（簡單，安全證明乾淨）

但 NIST 沒納 Schnorr（歷史原因 + 專利顧慮，雖已過期）。

## ECDH 應用：加密與密鑰封裝

ECDH 一般不直接當「加密」，而是**生成一次性 key**：

```python
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

# Alice 想加密訊息給 Bob，知道 Bob 的 X25519 公鑰
ephemeral = X25519PrivateKey.generate()
shared = ephemeral.exchange(bob_pub)

# Derive AES key
aes_key = HKDF(
    algorithm=hashes.SHA256(),
    length=32, salt=None,
    info=b"context"
).derive(shared)

# AES-GCM encrypt message
nonce = os.urandom(12)
ciphertext = aes_gcm_encrypt(aes_key, nonce, plaintext)

# 送出 (ephemeral.public_key, nonce, ciphertext) 給 Bob
```

Bob 解：用自己的 X25519 私鑰 + ephemeral_pub 算 shared → 同 KDF → 同 AES key → 解密。

**這就是 ECIES (Elliptic Curve Integrated Encryption Scheme)** 的骨架。`libsodium` 提供 `crypto_box`（X25519 + XSalsa20-Poly1305）一條 API。

## Curve 簽章 / 加密 cheat sheet

```
新系統推薦：
  簽章：Ed25519
  ECDH：X25519
  Hybrid encryption：libsodium crypto_box (X25519 + XSalsa20-Poly1305)
  
合規 / NIST 場景：
  簽章：ECDSA P-256（用 RFC 6979 deterministic）
  ECDH：ECDH P-256
  
Bitcoin / 區塊鏈：
  ECDSA secp256k1 / Schnorr
  
極長期 (50+ 年) key：
  Ed448
  或 SLH-DSA（post-quantum hash-based）
```

## 一個常見誤解

「Ed25519 比 ECDSA 安全度高」

**不是**。兩者都 ~128-bit security。**Ed25519 比 ECDSA 工程上更難寫錯**，但**正確實作的 ECDSA 安全度等同 Ed25519**。

Ed25519 的 advantage 是：

- 不需 RNG（deterministic）
- 不需 modular inversion（少一個 timing 點）
- 簡單規範（少出錯機會）

**「在現實工程下更難打爛自己腳」** ≠ 「algorithm 更強」。但工程上「更難打爛」就是足夠的選擇理由。

## 自我檢核

- [ ] 我能寫 ECDSA sign / verify
- [ ] 我能解釋 nonce reuse 怎麼洩漏私鑰（並算出 d）
- [ ] 我能說出 RFC 6979 deterministic nonce 怎麼避免上述問題
- [ ] 我能寫 Ed25519 sign / verify 步驟
- [ ] 我能比較 ECDSA P-256 與 Ed25519 的工程差異
- [ ] 我能用 X25519 + HKDF + AES-GCM 寫 ECIES 骨架

下一章看數位簽章與 PKI — 從演算法跳到「**整套信任體系**」。

→ [Ch 24 數位簽章與 PKI](./24-digital-signatures-pki.md)
