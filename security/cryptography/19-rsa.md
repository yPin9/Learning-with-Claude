# Ch 19 — RSA

> 目標：能從 Euler's theorem 推導 RSA 的正確性，理解 PKCS#1 v1.5 和 OAEP 的差異，手刻 RSA-2048 key generation。

---

## 為什麼需要 RSA

Ch 18 的 Diffie-Hellman 解決了 key exchange，但它不能做兩件事：

1. **加密**：DH 只能讓雙方「協議出」一把 key，無法直接加密任意訊息
2. **數位簽章**：DH 無法證明「這則訊息是某人發出的」

1977 年 Rivest、Shamir 和 Adleman 發表的 RSA 同時解決了這兩個問題：用公鑰加密、用私鑰解密；用私鑰簽章、用公鑰驗證。RSA 是第一個在學術界公開發表的完整公鑰密碼系統（GCHQ 的 Cocks 比他們早幾年發現了同樣的方案，但被列為機密）。

---

## 先建立直覺

RSA 的核心思想：

> 找兩個大質數 p 和 q，它們的乘積 n = pq 很容易算，但從 n 反推 p 和 q 非常困難（整數分解問題）。利用 Euler's theorem，我們可以構造一對運算——加密和解密——使得它們互為逆運算。

比喻：

```
鎖頭（公鑰）和鑰匙（私鑰）

Alice 製造一個鎖頭，把鑰匙留給自己
→ 任何人都可以用鎖頭鎖上箱子（加密）
→ 只有 Alice 有鑰匙能打開（解密）

鎖頭可以公開發放（public key）
鑰匙必須保密（private key）
```

---

## 核心概念：RSA 的數學

### 第一步：Key Generation

```
1. 選兩個大質數 p, q（各至少 1024 bit，使 n 至少 2048 bit）
2. 計算 n = p × q
3. 計算 φ(n) = (p-1)(q-1)     ← Euler's totient function
4. 選 e 使得 gcd(e, φ(n)) = 1  ← 常用 e = 65537 = 0x10001
5. 計算 d = e⁻¹ mod φ(n)       ← 用 Extended Euclidean Algorithm

公鑰：(n, e)
私鑰：(n, d)        實際上還要存 p, q（用於 CRT 加速）
```

### 第二步：加密與解密

```
加密（用公鑰）：c = m^e mod n
解密（用私鑰）：m = c^d mod n
```

### 正確性證明

為什麼 m^(ed) ≡ m (mod n)？

**Euler's Theorem（歐拉定理）**：若 gcd(m, n) = 1，則 m^φ(n) ≡ 1 (mod n)。

推導：
```
因為 ed ≡ 1 (mod φ(n))
所以 ed = 1 + k·φ(n)    （k 是某個正整數）

m^(ed) = m^(1 + k·φ(n))
       = m · (m^φ(n))^k

若 gcd(m, n) = 1：
  由 Euler's Theorem：m^φ(n) ≡ 1 (mod n)
  所以 m^(ed) ≡ m · 1^k = m (mod n)  ✓

若 gcd(m, n) ≠ 1（即 m 是 p 或 q 的倍數）：
  用 CRT 分開看 mod p 和 mod q
  例如 m ≡ 0 (mod p)：
    m^(ed) ≡ 0 ≡ m (mod p)  ✓
  m 跟 q 互質：
    m^(ed) ≡ m (mod q)       （Fermat's Little Theorem）  ✓
  由 CRT：m^(ed) ≡ m (mod n)  ✓
```

### 範例：小參數手算

```
p = 61, q = 53
n = 61 × 53 = 3233
φ(n) = 60 × 52 = 3120

選 e = 17（gcd(17, 3120) = 1 ✓）

d = 17⁻¹ mod 3120
用 Extended Euclidean：
  3120 = 183 × 17 + 9
  17 = 1 × 9 + 8
  9 = 1 × 8 + 1
  回推：1 = 9 - 8 = 9 - (17 - 9) = 2×9 - 17
       = 2×(3120 - 183×17) - 17 = 2×3120 - 367×17
  d = -367 mod 3120 = 2753

加密 m = 65：
  c = 65^17 mod 3233 = 2790

解密：
  m = 2790^2753 mod 3233 = 65  ✓
```

### 範例一：Python 手刻 RSA-2048

```python
"""RSA-2048 教學實作（完整版見練習 C）"""
import secrets
from math import gcd

def is_probable_prime(n, rounds=20):
    """Miller-Rabin"""
    if n < 2: return False
    if n < 4: return True
    if n % 2 == 0: return False
    r, d = 0, n - 1
    while d % 2 == 0: r += 1; d //= 2
    for _ in range(rounds):
        a = secrets.randbelow(n-3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n-1: continue
        for _ in range(r-1):
            x = pow(x, 2, n)
            if x == n-1: break
        else: return False
    return True

def gen_prime(bits):
    while True:
        n = secrets.randbits(bits) | (1 << (bits-1)) | 1
        if is_probable_prime(n): return n

def rsa_keygen(bits=2048):
    half = bits // 2
    while True:
        p, q = gen_prime(half), gen_prime(half)
        n = p * q
        if n.bit_length() != bits or abs(p-q).bit_length() < half-100:
            continue
        phi = (p-1)*(q-1); e = 65537
        if gcd(e, phi) != 1: continue
        d = pow(e, -1, phi)  # Python 3.8+
        return {'n':n, 'e':e, 'd':d, 'p':p, 'q':q,
                'd_p':d%(p-1), 'd_q':d%(q-1), 'q_inv':pow(q,-1,p)}

def rsa_decrypt_crt(c, key):
    """CRT 加速（快約 4 倍）"""
    m1 = pow(c, key['d_p'], key['p'])
    m2 = pow(c, key['d_q'], key['q'])
    h = (key['q_inv'] * (m1 - m2)) % key['p']
    return m2 + h * key['q']

# 演示
key = rsa_keygen(2048)
m = int.from_bytes(b"Hello RSA!", 'big')
c = pow(m, key['e'], key['n'])
assert pow(c, key['d'], key['n']) == m          # 普通解密
assert rsa_decrypt_crt(c, key) == m              # CRT 解密
print(f"RSA-2048 encrypt/decrypt OK, n={key['n'].bit_length()} bits")
```

---

## 底層機制

### CRT 加速的原理

RSA 解密的核心運算是 c^d mod n，其中 n 是 2048 bit。用 CRT 可以拆成兩個 1024-bit 的運算：

```
c^d mod n 拆成：
  m1 = c^(d mod (p-1)) mod p     ← 1024-bit 模冪
  m2 = c^(d mod (q-1)) mod q     ← 1024-bit 模冪

再用 CRT 合併：
  h = q_inv × (m1 - m2) mod p
  m = m2 + h × q

為什麼快 4 倍？
  模冪的時間 ∝ (bit 數)³
  一個 2048-bit 模冪 ≈ 2048³
  兩個 1024-bit 模冪 ≈ 2 × 1024³ = 2048³ / 4
```

生產環境的 RSA 實作（OpenSSL、mbedTLS）全部用 CRT 解密。

### e = 65537 的由來

```
e 的選擇要求：
  1. gcd(e, φ(n)) = 1
  2. 不能太小（e=3 有 Hastad attack 風險）
  3. 加密要快（e 的 hamming weight 小 → 模冪快）

65537 = 2^16 + 1 = 10000000000000001 (binary)
  只有 2 個 1 bit → square-and-multiply 只需 16 次 square + 1 次 multiply
  夠大，不會被低指數攻擊打
  是質數，幾乎一定跟 φ(n) 互質
```

### Textbook RSA 的安全問題

不帶 padding 的 RSA（稱為 textbook RSA）有多個致命問題：

```
問題 1：Deterministic
  同一個 m 每次加密得到同一個 c
  → 攻擊者可以建表：對所有可能的 m 算 m^e mod n
  → 如果 m 的空間小（例如是 0 或 1），直接查表

問題 2：Multiplicatively Homomorphic
  c1 = m1^e mod n
  c2 = m2^e mod n
  c1 × c2 = (m1 × m2)^e mod n
  → 攻擊者可以在不解密的情況下操縱密文

問題 3：m = 0 或 m = 1 不變
  0^e = 0, 1^e = 1
  → 這些值的「密文」洩露了明文

問題 4：沒有 semantic security
  Semantic security 要求：攻擊者給你兩個 m0, m1
  你加密其中一個，攻擊者不能判斷你加密的是哪個
  Textbook RSA 做不到（deterministic 就輸了）
```

---

## 進一步用法：Padding Schemes

### PKCS#1 v1.5 Padding（1993）

```
加密前的 padding 格式：
  0x00 | 0x02 | [至少 8 bytes 隨機非零 padding] | 0x00 | [明文]

總長度 = key 的 byte 數（RSA-2048 → 256 bytes）
明文最長 = 256 - 11 = 245 bytes

範例（RSA-2048, 明文 "Hi"）：
  00 02 [隨機 252 bytes, 都非零] 00 48 69
  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
         256 bytes total
```

PKCS#1 v1.5 解決了 deterministic 的問題（每次 padding 不同），但引入了新問題：**Bleichenbacher 1998 攻擊**。Ch 20 詳述。

### OAEP（Optimal Asymmetric Encryption Padding, 1994）

```
OAEP (Bellare-Rogaway) 的結構：

           ┌──────────┐
     m ──→ │ Pad to   │──→ DB (Data Block)
           │ fixed len│         │
           └──────────┘         │
                                ↓
                 seed ──→ [ MGF ] ──→ maskedDB
                   │                     │
                   ↓                     │
              [ MGF(maskedDB) ]          │
                   │                     │
                   ↓                     ↓
              maskedSeed            maskedDB
                   │                     │
                   └──────┬──────────────┘
                          ↓
                    RSA encrypt

MGF = Mask Generation Function（基於 SHA-256）
seed = 隨機產生
DB = lHash || PS || 01 || M
  lHash = SHA-256(label)
  PS = zero padding
  M = 原始明文
```

OAEP 的安全性：在 Random Oracle Model 下可證明具有 IND-CCA2 安全性——即使攻擊者可以要求 decryption oracle 解密任意密文（除了目標密文），仍然無法得知明文。

### PKCS#1 v1.5 vs OAEP

| 面向 | PKCS#1 v1.5 | OAEP |
|---|---|---|
| 標準年份 | 1993 | 1994 (PKCS#1 v2.0) |
| Padding oracle 抗性 | 脆弱（Bleichenbacher） | 可證明安全 |
| 安全模型 | 無正式證明 | IND-CCA2（Random Oracle） |
| 性能 | 稍快 | 需要兩次 MGF（忽略不計） |
| 現況 | 仍在大量使用（向後相容） | 新系統應使用 |
| TLS 1.3 | 已移除 | N/A（TLS 1.3 不用 RSA 加密） |

---

## 對比與取捨

### RSA vs DH

| 面向 | RSA | DH |
|---|---|---|
| 用途 | 加密 + 簽章 | key exchange |
| 數學基礎 | 整數分解 | 離散對數 |
| Forward secrecy | 不提供（static key） | Ephemeral DH 提供 |
| Key size（128-bit 安全） | 3072 bit | 3072 bit |
| TLS 1.3 | 只用於簽章 | 用於 key exchange（ECDHE） |

### RSA key size vs 安全等級

| RSA key size | 等效對稱 key 長度 | 現況 |
|---|---|---|
| 1024 bit | ~80 bit | **已不安全**，2010 年後不應使用 |
| 2048 bit | ~112 bit | 目前標準，NIST 建議用到 2030 |
| 3072 bit | ~128 bit | 長期安全 |
| 4096 bit | ~152 bit | 偏執級別 |
| 7680 bit | ~192 bit | 理論值，實務上太慢 |

---

## 踩雷集錦

### 雷 1：直接用 textbook RSA 加密

```python
# 錯誤：textbook RSA
c = pow(m, e, n)

# 正確：用 OAEP padding
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

ciphertext = public_key.encrypt(
    plaintext,
    padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None
    )
)
```

### 雷 2：p 和 q 太接近

如果 |p - q| 太小，Fermat factorization 可以在 O(|p-q|) 時間內分解 n：

```python
# Fermat factorization
def fermat_factor(n):
    """當 p ≈ q 時非常快"""
    from math import isqrt
    a = isqrt(n)
    if a * a == n:
        return a, a
    a += 1
    while True:
        b2 = a * a - n
        b = isqrt(b2)
        if b * b == b2:
            return a + b, a - b
        a += 1
```

生成 key 時要確保 |p - q| > 2^(bits/2 - 100)。

### 雷 3：e 太小且不加 padding

e = 3 加上不使用 padding → Hastad broadcast attack（Ch 20 詳述）。但 e = 3 **加上 OAEP** 是安全的。65537 是更穩的選擇。

### 雷 4：RSA 加密大量資料

RSA 一次只能加密 (key_size - padding_overhead) 的資料。RSA-2048 + OAEP-SHA256 只能加密 190 bytes。

正確做法：**hybrid encryption**。
```
1. 用 AES 生成隨機 session key k
2. 用 AES-GCM(k) 加密大量資料 → 密文 C
3. 用 RSA-OAEP 加密 k → 加密後的 key Ck
4. 傳送 (Ck, C)
```

### 雷 5：忘記 constant-time

RSA 解密的 CRT 實作如果不是 constant-time，會洩露 timing information。OpenSSL 的 RSA blinding 就是為了防 timing attack：

```
解密前：c' = c × r^e mod n  （r 是隨機數）
解密：  m' = (c')^d mod n = m × r mod n
還原：  m  = m' × r^(-1) mod n
```

攻擊者觀測不到 m 的解密時間，因為每次都被 r 隨機化了。

---

## 進階

### φ(n) vs λ(n)

嚴格來說，RSA 的正確性只需要 ed ≡ 1 (mod λ(n))，其中 λ(n) = lcm(p-1, q-1) 是 **Carmichael's totient function**。

```
φ(n) = (p-1)(q-1)          ← Euler's totient
λ(n) = lcm(p-1, q-1)       ← Carmichael's totient

λ(n) | φ(n)（λ(n) 整除 φ(n)）

用 λ(n) 算出的 d 通常更小 → 解密更快
但安全性相同
```

NIST FIPS 186-4 建議用 λ(n)。

### Multi-Prime RSA

n = p₁ × p₂ × ... × pₖ（k ≥ 3）。CRT 加速更明顯，但每個 prime 更小、分解更容易。k=3 搭配 RSA-4096 還行。

### RSA-PSS（簽章用 padding）

加密用 OAEP（IND-CCA2），簽章用 PSS（EUF-CMA）——跟 OAEP 類似的 random oracle 安全證明。別搞混。Ch 24 詳述。

---

## 動手練習

1. **手算 RSA**：取 p=61, q=53, e=17。計算 n, φ(n), d。加密 m=42，再解密回來。

2. **SageMath 驗證 CRT**：
   ```python
   p, q = 61, 53
   n = p * q; phi = (p-1)*(q-1)
   e = 17; d = inverse_mod(e, phi)
   m = 42; c = power_mod(m, e, n)
   # CRT 解密
   m1 = power_mod(c, d % (p-1), p)
   m2 = power_mod(c, d % (q-1), q)
   q_inv = inverse_mod(q, p)
   h = (q_inv * (m1 - m2)) % p
   result = m2 + h * q
   print(f"m={result}")  # 應該是 42
   ```

3. **Textbook RSA 攻擊**：加密 m=0, m=1, m=n-1 各得到什麼密文？為什麼這是問題？

4. **性能測試**：用 `cryptography` 套件的 RSA-2048 加密 vs AES-256-GCM 加密，比較 throughput。

5. **Fermat factorization**：生成一對 p, q 使得 |p-q| < 2^20。用 Fermat factorization 分解 n，記錄時間。

---

## 重點整理

```
RSA 基礎：
  n = p × q（兩個大質數的乘積）
  e = 65537（公鑰指數）
  d = e⁻¹ mod φ(n)（私鑰指數）
  加密：c = m^e mod n
  解密：m = c^d mod n

正確性依賴 Euler's Theorem：
  m^(ed) = m^(1+kφ(n)) = m × (m^φ(n))^k ≡ m (mod n)

CRT 加速解密（快 4 倍）：
  分別在 mod p 和 mod q 下算，再合併

Textbook RSA 不安全：
  deterministic / multiplicatively homomorphic / 沒有 semantic security

Padding 修復：
  PKCS#1 v1.5 → 有 Bleichenbacher 攻擊（Ch 20）
  OAEP → IND-CCA2 安全（新系統應使用）

RSA 只用來加密短資料（session key）：
  大量資料用 hybrid encryption（RSA 加密 AES key + AES 加密資料）
```

---

## 自我檢核

- [ ] 我能從 Euler's theorem 推導 RSA 的正確性
- [ ] 我能解釋 e = 65537 的選擇理由
- [ ] 我能解釋 CRT 加速為什麼快 4 倍
- [ ] 我能列舉 textbook RSA 的 3 個安全問題
- [ ] 我能區分 PKCS#1 v1.5 和 OAEP 的安全差異
- [ ] 我知道 RSA 不適合加密大量資料（要用 hybrid encryption）
- [ ] 我能解釋 Fermat factorization 對 |p-q| 太小的 key 的威脅
- [ ] 我知道 φ(n) 和 λ(n) 的差異

---

## 延伸閱讀

- **"A Method for Obtaining Digital Signatures and Public-Key Cryptosystems"**（RSA 原始論文, 1978）：可讀性高，數學不難
- **PKCS#1 v2.2 (RFC 8017)**：RSA 的完整標準——OAEP、PSS、v1.5 padding 都在裡面
- **Boneh & Shoup Ch 11**：RSA 的嚴格安全分析
- **"Twenty Years of Attacks on the RSA Cryptosystem"**（Dan Boneh, 1999）：經典攻擊總覽
- **CryptoHack RSA challenges**：https://cryptohack.org/challenges/rsa/

---

## 下一章連結

[Ch 20 — RSA 攻擊](./20-rsa-attacks.md)：你剛學了 RSA 怎麼運作。現在來看它怎麼被打穿——Wiener、Hastad、Bleichenbacher，每個攻擊打的是 RSA 的不同弱點。
