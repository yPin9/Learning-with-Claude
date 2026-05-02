# Ch 31 — ML-KEM (Kyber) 解剖

> 目標：把 NIST FIPS 203 的 ML-KEM（前身 Kyber）完整拆開：keygen / encapsulation / decapsulation 三個 algorithm、為什麼是 KEM 而非直接 PKE、Fujisaki-Okamoto 變形怎麼從 IND-CPA 升到 IND-CCA。

## ML-KEM 是什麼

**ML-KEM = Module-Lattice Key Encapsulation Mechanism**。

NIST FIPS 203（2024-08）。基於 Module-LWE（Ch 30）。

**KEM** = Key Encapsulation Mechanism：

```
Alice                            Bob
  │                               │
  │── ek (encap key, 公鑰) ──────▶│
  │                               │
  │       Bob: encap(ek) → (ct, K)
  │                               │
  │◄────── ct (ciphertext) ──────│
  │                               │
  │  Alice: decap(dk, ct) → K     │
  │                               │
  │           K (shared secret)   │
```

KEM 給 **shared secret K**，不直接加密 message（要再用 K + AEAD 加密真實訊息）。

vs PKE（直接加密訊息）：KEM 設計上更簡單、安全證明更乾淨。**現代公鑰密碼大宗是 KEM 模式**。

## 三個參數變體

```
                   Level    Public key   Ciphertext   Secret key
ML-KEM-512         1        800 byte     768 byte     1632 byte
ML-KEM-768         3        1184 byte    1088 byte    2400 byte
ML-KEM-1024        5        1568 byte    1568 byte    3168 byte
```

對比 X25519：32 byte public key、32 byte ciphertext。**Kyber 大 25-50 倍**。

但 **比 RSA-2048（256 byte key）只大 3-6 倍**，可接受。

實務多選 **ML-KEM-768**（Level 3 = 192-bit security 對 quantum）。

## ML-KEM 內部參數

```
n = 256   (polynomial degree)
q = 3329  (modulus, prime)
k = 2/3/4 for ML-KEM-512/768/1024
```

每個 polynomial 是 R_q = Z_3329[x] / (x^256 + 1) 的元素。

## Algorithm 1：KeyGen

```
KeyGen():
  1. Generate seed d ∈ {0,1}^256
  2. Expand d via SHAKE-256 to get matrix A ∈ R_q^(k×k)
  3. Sample s, e ∈ R_q^k from CBD (centered binomial distribution)
  4. Compute t = A @ s + e (in R_q^k)
  5. Encode public key: ek = (A's seed) || encode(t)
  6. Encode secret key: dk = encode(s)  (plus extra for FO transform)
  return (ek, dk)
```

**核心數學**：`t = A @ s + e` 是 Module-LWE 樣本。`s` 是 secret，`A`、`t` 公開。安全性 = Module-LWE 假設。

`A` 從 seed 派生（不是直接送），**省 bandwidth**。

## Algorithm 2：Encapsulation

```
Encaps(ek):
  1. m ∈ {0,1}^256 隨機（會變 shared secret）
  2. (K, r) = G(m, H(ek))   ← G is a hash, K is shared, r is randomness
  3. (u, v) = K-PKE.encrypt(ek, m, r)  ← lattice PKE encrypt
  4. ct = encode(u, v)
  return (K, ct)
```

K-PKE.encrypt 內部：

```
Encrypt(ek, m, r):
  Sample r', e1, e2 from CBD using seed r
  u = A^T @ r' + e1
  v = t^T @ r' + e2 + Compress(decode(m))
  return (u, v)
```

**直覺**：`u, v` 是兩組 LWE 樣本，把 m 加密在 `v` 中，r' 是 ephemeral randomness。

**Compress**：把 m 從 256-bit 串展開到 polynomial domain；decompress 反向。

## Algorithm 3：Decapsulation

```
Decaps(dk, ct):
  1. m' = K-PKE.decrypt(dk, ct)
  2. (K', r') = G(m', H(ek))
  3. ct' = K-PKE.encrypt(ek, m', r')   ← 重新算一次 ct
  4. if ct' == ct: return K'           ← 正常 path
     else: return implicit reject K_bar
```

K-PKE.decrypt 內部：

```
Decrypt(dk, (u, v)):
  return Decompress(v - s^T @ u)
```

直覺：`v - s^T u = (t^T r' + e2 + ...) - s^T (A^T r' + e1)`，整理後 ≈ encoded(m) + small_error。Decompress 還原 m。

## Fujisaki-Okamoto Transform：CPA → CCA

**K-PKE 自身只有 IND-CPA 安全**。直接用會被 chosen-ciphertext 攻擊。

**Fujisaki-Okamoto (FO) transform** 把它升到 IND-CCA：

關鍵 idea：**re-encryption check**。decap 時不只解出 m'，還用 m' 重 encrypt 一次得到 ct'，比對：

- 等於原 ct → 正常
- 不等 → attacker 改了 ct，**返回 implicit reject**（一個 deterministic 但無關 K）

FO 的精髓在 **「implicit reject」而非 explicit error**：attacker 看不出 ct 是合法還是被她改的（都回某個 K，只是不同 K）。**抗 chosen-ciphertext attack**。

## 為什麼 re-encryption 安全

```
attacker 如果改 ct → 解出 m' 但 ct' ≠ ct → reject
attacker 如果不改 ct → 解出原 m → ct' == ct → 正常 K
```

她**沒法**「改一點 ct 看看是不是同 K」 — 任何修改都導致 reject。

CCA security 的精髓：**沒有「部分 valid」的 ciphertext**。

## 完整流程例子

```python
# Pseudocode 對 ML-KEM-768
ek, dk = ML_KEM_768.keygen()
print(f"public key: {len(ek)} bytes")     # 1184
print(f"secret key: {len(dk)} bytes")     # 2400

# Bob encapsulates
shared_K, ct = ML_KEM_768.encaps(ek)
print(f"ct: {len(ct)} bytes")             # 1088
print(f"shared secret: {shared_K.hex()}")  # 32 bytes

# Alice decapsulates
shared_K_alice = ML_KEM_768.decaps(dk, ct)
assert shared_K_alice == shared_K

# 用 K + AEAD 加密真實訊息
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
nonce = os.urandom(12)
ct_message = ChaCha20Poly1305(shared_K).encrypt(nonce, b"secret", b"")
```

## CBD (Centered Binomial Distribution) 採樣

LWE 的 noise 分布。Kyber 用 CBD η（η = 2 或 3）：

```
sample η bits a, η bits b (uniformly)
return sum(a) - sum(b)   // mean 0, small std
```

更簡單比 Gaussian 採樣（後者要 const-time 處理 reject sampling）。

## NTT：speedup

Kyber 內部用 **Number-Theoretic Transform** 做 polynomial multiplication。對 256-degree polynomial，普通 O(n²) → NTT O(n log n)。

NTT 是 FFT 的整數版（在 Z_q 而非複數上）。實作時 polynomial 永遠以 NTT 表示，乘法直接 pointwise，需要時 inverse NTT。

C/Rust 實作大量用 NTT，性能直追 X25519。

## 安全等級

```
ML-KEM-512:  NIST Level 1 ≈ AES-128 對 quantum
ML-KEM-768:  Level 3 ≈ AES-192
ML-KEM-1024: Level 5 ≈ AES-256
```

預設選 768（multi-decade security）。1024 給「**未來幾十年都 safe**」場景。

## 與 hybrid X25519+Kyber

實務多用：

```python
shared_X = X25519.exchange()  # 32 byte
shared_K = ML_KEM_768.decaps()  # 32 byte
final_key = HKDF(shared_X || shared_K, info=b"hybrid")
```

任一被破，攻擊者得不到 final_key。**Cloudflare、Chrome 用這套**。

## 性能（typical）

```
Operation         Time (modern CPU, single core)
KeyGen            ~50 µs
Encaps            ~70 µs
Decaps            ~80 µs
```

**比 X25519 慢 2-3 倍**（X25519 ~25 µs / 30 µs）。但仍快過 RSA-2048 keygen（~5ms）。

## 實作工具

```
liboqs (Open Quantum Safe):
  C library, multi-language bindings
  github.com/open-quantum-safe/liboqs
  
pq-crystals/kyber:
  reference + AVX2 + ARM optimized
  github.com/pq-crystals/kyber
  
PQClean:
  cleaned-up reference implementations
  github.com/PQClean/PQClean
  
Python:
  kyber-py (academic), pqcrypto
```

## 一個常見誤解

「ML-KEM 比 X25519 還好嗎？」

**short term：用 hybrid（X25519 + ML-KEM）**。X25519 仍夠抗古典 attacker；ML-KEM 補 quantum。

**long term（2030+）**：純 ML-KEM 取代 X25519。**現在還太早全切**。

對 long-lived secret（10+ 年）：**現在就要 hybrid**。short-lived secret（HTTPS session）：可選 X25519 自己撐到 quantum 來臨。

## 自我檢核

- [ ] 我能說出 ML-KEM 三個算法（KeyGen, Encaps, Decaps）
- [ ] 我能解釋 KEM 與 PKE 的差別
- [ ] 我能說出 ML-KEM 三個變體的 key/ct size
- [ ] 我能解釋 Fujisaki-Okamoto re-encryption check 為什麼達 IND-CCA
- [ ] 我能說出 ML-KEM 與 X25519 性能差距
- [ ] 我知道 hybrid X25519+Kyber 怎麼組合

下一章看 ML-DSA (Dilithium) 簽章。

→ [Ch 32 ML-DSA (Dilithium)](./32-ml-dsa-dilithium.md)
