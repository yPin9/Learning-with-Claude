# Ch 23 — ECDSA / EdDSA / X25519

> 目標：能區分 ECDSA / EdDSA / X25519 的用途和安全差異，解釋 nonce reuse 對 ECDSA 的災難（Sony PS3 案例）。

---

## 為什麼需要這章

Ch 22 建立了橢圓曲線的數學基礎。現在看三個實際應用：

| 名稱 | 用途 | 重點 |
|---|---|---|
| **ECDSA** | 數位簽章 | TLS / X.509 / Bitcoin 的主力簽章 |
| **EdDSA (Ed25519)** | 數位簽章 | Deterministic nonce，解決 ECDSA 的致命缺陷 |
| **X25519** | 金鑰交換（ECDH） | TLS 1.3 / Signal / WireGuard 的 ECDH |

三者都建立在 ECDLP 的困難性上，但用途和設計哲學差異很大。

---

## 先建立直覺

數位簽章的概念：

```
Alice 有 private key（只有她知道）和 public key（所有人都知道）

簽章：Alice 用 private key 對 message 產生 signature
驗證：任何人用 Alice 的 public key 驗證 signature

性質：
  1. 只有 Alice 能產生合法 signature（因為只有她有 private key）
  2. 任何人都能驗證（用 public key）
  3. 無法偽造（EUF-CMA security）
```

ECDH 的概念（復習 Ch 18，但用橢圓曲線取代 Z_p*）：

```
DH over Z_p*：                    ECDH over E(GF(p))：
  g^a mod p                        aG（scalar multiplication）
  g^b mod p                        bG
  g^(ab) mod p                     abG = a(bG) = b(aG)
```

---

## 核心概念一：ECDSA

### 簽章流程

```
曲線參數：G（基點，order n）
Alice 的私鑰：d（隨機數 ∈ [1, n-1]）
Alice 的公鑰：Q = dG

簽章 message M：
  1. 計算 hash：e = SHA-256(M)
  2. 選隨機 nonce k ∈ [1, n-1]        ← 這個 k 極其關鍵！
  3. 計算 R = kG，取 r = R.x mod n
  4. 計算 s = k⁻¹ × (e + r × d) mod n
  5. 簽章 = (r, s)

驗證：
  1. 計算 e = SHA-256(M)
  2. 計算 u₁ = e × s⁻¹ mod n
  3. 計算 u₂ = r × s⁻¹ mod n
  4. 計算 R' = u₁G + u₂Q
  5. 檢查 R'.x mod n == r

正確性：
  R' = u₁G + u₂Q
     = (e × s⁻¹)G + (r × s⁻¹)(dG)
     = s⁻¹ × (e + rd)G
     = s⁻¹ × (k × s)G      （因為 s = k⁻¹(e + rd)）
     = kG = R  ✓
```

### SageMath 實作

```python
# SageMath — ECDSA on secp256k1
p = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
E = EllipticCurve(GF(p), [0, 7])
n = E.order()
G = E.lift_x(0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798)
d = 12345; Q = d * G

# 簽章
import hashlib
e = int(hashlib.sha256(b"Hello ECDSA").hexdigest(), 16) % n
k = 98765
R = k * G; r = int(R[0]) % n
s = (inverse_mod(k, n) * (e + r * d)) % n

# 驗證
s_inv = inverse_mod(s, n)
R_check = (e * s_inv % n) * G + (r * s_inv % n) * Q
print(f"Valid: {int(R_check[0]) % n == r}")
```

---

## 底層機制：Nonce Reuse 的數學災難

### 如果 k 被重複使用

假設 Alice 用同一個 k 簽了兩則不同的 message：

```
簽章 1：(r, s₁) where s₁ = k⁻¹(e₁ + r·d) mod n
簽章 2：(r, s₂) where s₂ = k⁻¹(e₂ + r·d) mod n

注意：r 相同（因為 r = (kG).x，k 相同 → r 相同）

s₁ - s₂ = k⁻¹(e₁ - e₂) mod n

→ k = (e₁ - e₂) × (s₁ - s₂)⁻¹ mod n

已知 k 後：
  d = (s₁ × k - e₁) × r⁻¹ mod n

→ 私鑰 d 完全暴露！
```

**一次 nonce reuse = 私鑰洩露**。沒有 if、沒有 but。

### Sony PS3 案例（2010）

```
時間線：
  2006: Sony 發售 PS3，遊戲和系統更新用 ECDSA 簽章
  2010: 黑客組 fail0verflow 在 CCC 大會上公開 PS3 的私鑰
  原因: Sony 的 ECDSA 實作中，nonce k 是一個固定值

Sony 的「隨機數生成器」：
  int get_random(void) {
      return 4;  // chosen by fair dice roll.
                  // guaranteed to be random.
  }

  （這不是笑話——Sony 的實際 code 用了一個常數 k）

後果：
  - PS3 的安全模型徹底崩潰
  - 任何人都可以簽署 custom firmware
  - Homebrew 和盜版遊戲在 PS3 上執行
  - Sony 無法透過軟體更新修復（私鑰已洩露）
```

### Python PoC：Nonce Reuse Attack

```python
"""ECDSA nonce reuse → 私鑰恢復"""
import hashlib

def nonce_reuse_attack(n, r, s1, s2, e1, e2):
    """已知 (r,s1) 和 (r,s2)（同 k），恢復 d"""
    k = ((e1 - e2) * pow(s1 - s2, -1, n)) % n
    d = ((s1 * k - e1) * pow(r, -1, n)) % n
    return k, d

# secp256k1 order
n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
d_real = 0xDEADBEEF12345678CAFEBABE87654321
e1 = int(hashlib.sha256(b"msg 1").hexdigest(), 16) % n
e2 = int(hashlib.sha256(b"msg 2").hexdigest(), 16) % n
k = 0x4242424242424242  # Sony 式固定 nonce
r = 0xABCDEF0123456789  # 模擬 r = (kG).x
k_inv = pow(k, -1, n)
s1 = (k_inv * (e1 + r * d_real)) % n
s2 = (k_inv * (e2 + r * d_real)) % n

k_found, d_found = nonce_reuse_attack(n, r, s1, s2, e1, e2)
print(f"k correct: {k_found == k}, d correct: {d_found == d_real}")
# 一次 nonce reuse = 私鑰完全暴露
```

---

## 核心概念二：EdDSA (Ed25519)

### 設計哲學：消除 nonce reuse 風險

EdDSA 的核心改進：**nonce 是 deterministic 的**，從 private key 和 message 的 hash 派生。

```
EdDSA 簽章流程：

  私鑰：seed（32 bytes）
  h = SHA-512(seed)
  a = h[0:32]（clamped：設定/清除特定 bit）→ scalar
  prefix = h[32:64] → 用來生成 nonce

  公鑰：A = aB（B 是基點）

  簽章 message M：
    1. r = SHA-512(prefix || M)          ← deterministic！
    2. R = rB
    3. S = (r + SHA-512(R || A || M) × a) mod l
    4. 簽章 = (R, S)

  驗證：
    SB == R + SHA-512(R || A || M) × A ?
```

### 為什麼 deterministic nonce 更安全

```
ECDSA 的 nonce：
  必須是真隨機數
  任何偏差（bias）都可能被利用
  RNG 壞了 → 私鑰洩露
  k 重複 → 私鑰洩露

EdDSA 的 nonce：
  r = SHA-512(prefix || M)
  prefix 是 private key 的一部分（外人不知道）
  M 是 message
  → 只要 SHA-512 沒被破，r 看起來是隨機的
  → 同一個 message 永遠產生同一個 r（deterministic）
  → 不依賴 RNG → 沒有 RNG 失敗的風險
```

### ECDSA vs EdDSA 比較

| 面向 | ECDSA | EdDSA (Ed25519) |
|---|---|---|
| Nonce 生成 | 必須是真隨機 | Deterministic（from key + msg） |
| Nonce reuse | 致命（洩露私鑰） | 不可能（same msg → same nonce） |
| 依賴 RNG | 是（簽章時） | 否（只有 keygen 需要 RNG） |
| 曲線 | P-256, secp256k1 等 | Curve25519 (Ed25519) |
| 驗證速度 | 中等 | 快（batch verification 更快） |
| Malleability | 有（s 可以被翻轉） | 有限制（可以排除） |
| 標準 | FIPS 186-4, ANSI X9.62 | RFC 8032 |
| 採用 | TLS, X.509, Bitcoin | SSH, Signal, Tor, Minisign |

### Ed25519 的具體性能

```
Ed25519（在現代 x86_64 上）：
  Keygen:      ~52 μs
  Sign:        ~56 μs
  Verify:      ~170 μs
  Batch verify (64 sigs): ~2.1 ms（~33 μs/sig）

對比 ECDSA P-256（OpenSSL）：
  Sign:        ~40 μs
  Verify:      ~130 μs
```

### RFC 6979：ECDSA 的 Deterministic Nonce 補丁

ECDSA 後來也有了 deterministic nonce 的方案（RFC 6979）：

```
k = HMAC-DRBG(private_key, message_hash)

跟 EdDSA 的思路一樣：用私鑰和 message 派生 nonce。
但 EdDSA 從一開始就是這樣設計，而 ECDSA 是事後補丁。
```

---

## 核心概念三：X25519（ECDH）

### 協議流程

```
X25519 = ECDH 使用 Curve25519

Alice                              Bob
─────                              ────
1. a = random 32 bytes            1. b = random 32 bytes
   a = clamp(a)                      b = clamp(b)
2. A = X25519(a, G)               2. B = X25519(b, G)
   （只保留 x 座標）                  （只保留 x 座標）
3. 發送 A ──────────────────────> 收到 A
4. 收到 B <────────────────────── 發送 B
5. shared = X25519(a, B)          5. shared = X25519(b, A)

shared = a(bG) = b(aG) = abG  ✓
```

### X25519 的特殊設計

```
1. 只用 x 座標（Montgomery 曲線的優勢）
   → 公鑰只有 32 bytes（不是 64）
   → 加法和 doubling 只需要 x 座標

2. Key clamping
   key[0] &= 248     # 最低 3 bit 清零 → cofactor clearing
   key[31] &= 127    # 最高 bit 清零
   key[31] |= 64     # 第二高 bit 設為 1 → 確保 key 長度固定

3. 所有 32-byte string 都是合法的公鑰
   → 不需要驗證對方的公鑰（all-zero 除外）
   → 簡化實作，減少出錯空間
```

### Python 使用 X25519

```python
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

# Key exchange
alice_priv = X25519PrivateKey.generate()
bob_priv = X25519PrivateKey.generate()
alice_shared = alice_priv.exchange(bob_priv.public_key())
bob_shared = bob_priv.exchange(alice_priv.public_key())
assert alice_shared == bob_shared  # 32 bytes

# 永遠用 KDF 派生 key（shared secret 不是 uniform random）
aes_key = HKDF(algorithm=hashes.SHA256(), length=32,
               salt=None, info=b"handshake").derive(alice_shared)
```

---

## 對比與取捨

### 三者的用途分工

```
                  簽章           金鑰交換
                  ────           ────────
  ECDSA           ✓               ✗
  EdDSA           ✓               ✗
  X25519          ✗               ✓

在 TLS 1.3 中的角色：
  handshake key exchange → X25519（或 P-256 ECDH）
  server 身份認證        → Ed25519 簽章（或 ECDSA P-256）
  certificate chain      → ECDSA P-256（目前主流，Ed25519 逐漸增加）
```

### 選擇指南

| 你需要什麼 | 用什麼 | 理由 |
|---|---|---|
| ECDH key exchange | X25519 | 最快、最安全、最廣泛支持 |
| 數位簽章（新系統） | Ed25519 | Deterministic nonce、不依賴 RNG |
| 數位簽章（相容性） | ECDSA P-256 | X.509 / CA 生態系統的主力 |
| 更高安全等級 | X448 / Ed448 | ~224-bit 安全 |
| Bitcoin | ECDSA secp256k1 | 歷史原因（Schnorr via Taproot 逐步替代） |

### 各方案在 TLS 1.3 中的支援

| 方案 | TLS 1.3 KeyShare | TLS 1.3 SignatureScheme |
|---|---|---|
| X25519 | ✓ (0x001D) | — |
| P-256 ECDH | ✓ (0x0017) | — |
| ECDSA P-256 SHA-256 | — | ✓ (0x0403) |
| Ed25519 | — | ✓ (0x0807) |

---

## 踩雷集錦

### 雷 1：ECDSA 的 malleability

```
ECDSA 簽章 (r, s) 中，(r, n-s) 也是合法簽章。
→ 第三方可以「修改」簽章但仍通過驗證
→ Bitcoin 的 transaction malleability 問題的根源之一

防禦：要求 s ≤ n/2（low-s normalization）
Bitcoin 從 BIP 62 / BIP 66 開始強制執行
```

### 雷 2：用 ECDSA 時不用 RFC 6979

```python
# 錯誤：自己生隨機 k
k = random.randint(1, n-1)  # random 模組不是 CSPRNG！

# 正確：用 RFC 6979
# 或者直接用 Ed25519（從根本上解決問題）
```

即使用了 CSPRNG，只要 RNG 有微小偏差（bias），也可能被利用。Minerva 攻擊（2019）展示了 timing side-channel 導致 nonce 有偏差，可以用 lattice attack 恢復私鑰。

### 雷 3：Ed25519 的 clamping 不能省略

```python
# 錯誤：直接用原始 seed 當 scalar
a = int.from_bytes(seed, 'big')

# 正確：clamping
h = sha512(seed)
a = h[:32]
a[0] &= 248
a[31] &= 127
a[31] |= 64
```

Clamping 保證：(1) scalar 是 8 的倍數（cofactor clearing），(2) scalar 的 bit 長度固定（constant-time）。

### 雷 4：X25519 的 shared secret 直接當 key

```python
# 錯誤
aes_key = x25519_shared_secret[:16]

# 正確
aes_key = HKDF(SHA256, length=16, salt=..., info=...).derive(x25519_shared_secret)
```

X25519 的 shared secret 不是 uniform random——它是一個 x 座標。必須用 KDF 派生。

### 雷 5：以為 Ed25519 和 X25519 的 key pair 可以互換

Ed25519 和 X25519 用的是同一條曲線（birational equivalent），但 key format 不同。不能直接把 Ed25519 的 private key 拿來做 X25519。需要做座標轉換：

```python
# 有些 library 提供轉換
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
# 但 Python cryptography 不直接支援跨格式使用
# Signal Protocol 和 libsodium 有 crypto_sign_ed25519_pk_to_curve25519()
```

---

## 進階

### Schnorr Signature

Ed25519 本質上是 Schnorr 的變種。Schnorr 的優勢：可證明安全（ROM 下等同 ECDLP）、線性（可做 multi-sig / threshold sig）、batch verification、數學比 ECDSA 乾淨。Bitcoin 在 2021 Taproot 升級（BIP 340）加入 Schnorr，逐步取代 ECDSA。

### Batch Verification

Ed25519 支援 batch verification：n=64 時約 2-3x 加速。Signal 和 Tor 用此加速大量簽章驗證。

### Minerva Attack（2019）

ECDSA 的 nonce 若有微小 timing bias → 收集數千簽章 → lattice attack（Hidden Number Problem）恢復私鑰。受影響：OpenSSL 某些版本、Java Card、某些 HSM。EdDSA 的 deterministic nonce 完全免疫。

---

## 動手練習

1. **ECDSA nonce reuse**：用 SageMath 在 secp256k1 上簽兩則 message（用同一個 k），然後從兩個簽章恢復私鑰。

2. **Ed25519 簽章與驗證**：用 Python `cryptography` 套件做 Ed25519 的完整流程。
   ```python
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
   key = Ed25519PrivateKey.generate()
   sig = key.sign(b"Hello Ed25519")
   key.public_key().verify(sig, b"Hello Ed25519")
   ```

3. **X25519 key exchange**：實作完整的 ECDH key exchange + HKDF 派生 AES key + AES-GCM 加密 message。

4. **比較簽章大小**：用 `cryptography` 套件分別做 RSA-2048、ECDSA P-256、Ed25519 簽章，比較簽章長度和速度。

5. **Malleability 驗證**：在 ECDSA 簽章 (r, s) 中，驗證 (r, n-s) 也能通過驗證。

---

## 重點整理

```
ECDSA：
  簽章 = (r, s)，r 從 nonce kG 的 x 座標來
  致命弱點：nonce reuse → 私鑰洩露（Sony PS3 案例）
  修補：RFC 6979（deterministic nonce）
  仍在大量使用：TLS、X.509、Bitcoin

EdDSA (Ed25519)：
  Deterministic nonce = SHA-512(prefix || message)
  從設計上消除 nonce reuse 風險
  不依賴 RNG（簽章時）
  Ed25519 = Schnorr variant on Curve25519

X25519：
  ECDH key exchange on Curve25519
  Montgomery ladder → constant-time
  只用 x 座標 → 公鑰 32 bytes
  TLS 1.3 的首選 key exchange

選擇優先順序（新系統）：
  簽章 → Ed25519
  金鑰交換 → X25519
  相容性需求 → ECDSA P-256 / ECDH P-256
```

---

## 自我檢核

- [ ] 我能寫出 ECDSA 簽章和驗證的數學流程
- [ ] 我能從兩個 nonce reuse 的 ECDSA 簽章推導出私鑰
- [ ] 我能解釋 EdDSA 如何消除 nonce reuse 風險
- [ ] 我能區分 ECDSA / EdDSA / X25519 的用途
- [ ] 我知道 Sony PS3 事件的技術細節
- [ ] 我能解釋 X25519 的 key clamping 為什麼重要
- [ ] 我知道 ECDSA 的 signature malleability 問題

---

## 延伸閱讀

- **RFC 8032**：EdDSA（Ed25519 / Ed448）的完整規範
- **RFC 7748**：X25519 / X448 的完整規範
- **RFC 6979**：ECDSA 的 deterministic nonce
- **BIP 340**：Bitcoin 的 Schnorr signature 規範
- **"Console Hacking 2010: PS3 Epic Fail"**（fail0verflow, CCC 2010）：Sony PS3 案例的完整演示
- **CryptoHack ECDSA challenges**：https://cryptohack.org/challenges/ecdsa/

---

## 下一章連結

[Ch 24 — 數位簽章與 PKI](./24-digital-signatures-pki.md)：你已經知道 ECDSA 和 EdDSA 怎麼運作了。但「數位簽章」只是拼圖的一半——另一半是「我怎麼知道這把公鑰是誰的？」。PKI、X.509 certificate、CA chain 就是解這個問題的。
