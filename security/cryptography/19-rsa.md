# Ch 19 — RSA：Euler totient、CRT 加速、padding 模式

> 目標：搞懂 RSA 的數學基礎（Euler 定理、為什麼 m^(ed) ≡ m mod n）、CRT 加速 4× 的實作技巧、textbook RSA 為什麼絕對不能用、PKCS#1 v1.5 vs OAEP padding 的選擇。

## RSA 簡史

```
1976  Diffie-Hellman 提出公鑰概念但沒給具體 encryption 算法
1977  Rivest, Shamir, Adleman 在 MIT 提 RSA
1978  paper "A Method for Obtaining Digital Signatures and Public-Key Cryptosystems"
1983  RSA 美國專利
2000  專利到期
```

**RSA 是公鑰密碼史上第一個真正可用的算法**。長達 30 年是公鑰密碼代名詞。直到 ECC 在 2010 年代漸取代 RSA。

## RSA Key Generation

```
1. 選兩個大質數 p, q（如 1024-bit 各自，總 n 為 2048-bit）
2. n = p × q
3. φ(n) = (p-1)(q-1)   ← Euler totient
4. 選 e 與 φ(n) 互質（常用 65537 = 0x10001）
5. d = e⁻¹ mod φ(n)    ← 用 ext-Euclid

公鑰 = (n, e)
私鑰 = (n, d)（或 (p, q, d) 加速版）
```

```python
import secrets
from sympy import isprime, mod_inverse

def gen_prime(bits):
    while True:
        n = secrets.randbits(bits) | 1 | (1 << (bits-1))
        if isprime(n):
            return n

def rsa_keygen(bits=2048):
    p = gen_prime(bits // 2)
    q = gen_prime(bits // 2)
    while q == p:
        q = gen_prime(bits // 2)
    n = p * q
    phi = (p - 1) * (q - 1)
    e = 65537
    d = mod_inverse(e, phi)
    return (n, e), (n, d, p, q)
```

## RSA Encryption / Decryption

```
Encryption: c = m^e mod n
Decryption: m = c^d mod n
```

數學上正確：`(m^e)^d = m^(ed) ≡ m (mod n)`。

證明用 Euler 定理：

```
ed ≡ 1 (mod φ(n))   ← d 是 e 的 inverse mod φ(n)
故 ed = 1 + k × φ(n)  對某 k

m^(ed) = m^(1 + k×φ(n)) = m × m^(k×φ(n)) = m × (m^φ(n))^k

由 Euler 定理：gcd(m, n) = 1 時 m^φ(n) ≡ 1 (mod n)
故 m^(ed) ≡ m × 1^k = m (mod n) ∎
```

注意 `gcd(m, n) = 1`。**極小機率 m 與 n 共因子**（例如 m = p 或 m = q），但 m < n 且 m 隨機選時機率 ≈ 0。

## 簽章：解密 / 加密的對偶

```
Sign:    s = m^d mod n   (用私鑰)
Verify:  m = s^e mod n   (用公鑰)
```

實務上不會直接簽 m，會先 hash：`s = H(m)^d mod n`。Ch 20-24 詳述。

## CRT 加速：解密快 4×

直接算 `c^d mod n` 對 2048-bit n 約 5 ms。**用 CRT 可以加速到 1.2 ms**。

原理：CRT 把 mod n 拆成 mod p + mod q：

```
m_p = c^(d mod (p-1)) mod p
m_q = c^(d mod (q-1)) mod q

m = CRT 合併 (m_p, m_q)   給出 mod (pq) = mod n 的解
```

每個 mod p / mod q 是 1024-bit，比 mod 2048 快 4×（指數運算 cubic complexity）。再加上 CRT 合併開銷，總共加速約 4×。

```python
def rsa_decrypt_crt(c, p, q, d):
    dp = d % (p - 1)
    dq = d % (q - 1)
    qinv = pow(q, -1, p)
    
    mp = pow(c, dp, p)
    mq = pow(c, dq, q)
    h = (qinv * (mp - mq)) % p
    m = mq + h * q
    return m
```

**所有 production RSA 都用 CRT**。OpenSSL、Python `cryptography`、libsodium 全部。

## CRT 帶來的 fault attack 風險

CRT 加速有個副作用 — **如果計算錯一點，attacker 能因式分解 n**：

```
假設 m_p 算對、m_q 算錯（hardware fault、glitch）
attacker 拿到 faulty signature s'
正確 s = m^d mod n

s ≡ s' (mod p)   ← p 部分對
s ≢ s' (mod q)   ← q 部分錯

gcd(s - s', n) = p   ← 因式分解 n！
```

實務 mitigation：**簽章後 verify**（簽完用公鑰驗一次，發現錯就 abort）。Production RSA library 都做這個。

## 簡單 RSA encrypt 範例

```python
def rsa_encrypt(msg_int, n, e):
    return pow(msg_int, e, n)

def rsa_decrypt(ct_int, n, d):
    return pow(ct_int, d, n)

# Toy example
pub, priv = rsa_keygen(bits=512)  # 不安全大小，只 demo
n, e = pub
n, d, p, q = priv

m = 12345
c = rsa_encrypt(m, n, e)
m2 = rsa_decrypt(c, n, d)
assert m2 == m
```

## Textbook RSA：絕對不能直接用

```python
# 危險 — textbook RSA
c = pow(m_int, e, n)
```

問題一堆：

### 1. 確定性 → 不滿足 IND-CPA

同 m + 同 (n, e) 永遠同 c。攻擊者能枚舉 plaintext 候選驗證。

### 2. 小 m + 小 e → 直接開根號

`m^3 mod n`，若 `m^3 < n` → c = m^3，攻擊者直接 cube root。

```python
m = 5
e = 3
n = ... # 2048-bit
c = pow(m, e, n)  # = 125
# attacker: cbrt(125) = 5 ← 不需 d
```

### 3. Multiplicative：m1 × m2 → c1 × c2

```
RSA(m1) × RSA(m2) = m1^e × m2^e = (m1 m2)^e = RSA(m1 × m2)
```

attacker 拿 c1, c2 可算 RSA(m1 × m2) — chosen-ciphertext attack 用這個放大。

### 4. 沒 randomness → 一個密文洩漏全部

只能加密 < n 的訊息，且洩漏 m 的數學結構。

**任何 RSA 用法必加 padding**。

## PKCS#1 v1.5 padding（舊但廣用）

```
plaintext m (k byte 訊息，模長 n_byte byte)
padded = 0x00 || 0x02 || PS || 0x00 || m

PS = padding string，至少 8 byte 隨機非零 byte
```

例：

```
PKCS1_pad("Hello", n_byte=128) =
  0x00 0x02 <120 byte random non-zero> 0x00 H e l l o
```

加密：`c = pow(int(padded), e, n)`

解密 + unpad：

```
1. m_padded = pow(c, d, n)
2. 確認 m_padded[0] == 0x00 且 m_padded[1] == 0x02
3. 找 m_padded 裡第一個 0x00（從 index 2 之後）
4. 後面就是 plaintext
```

**Bleichenbacher 1998 padding oracle attack**：server 對 padding 對 / 錯回不同錯誤訊息 → attacker 能逐 bit 推算出整段 plaintext。Ch 20 詳述。

PKCS#1 v1.5 在 ROBOT (2017) 復活攻擊 — TLS 1.2 仍可用 PKCS#1 v1.5 RSA encryption，被 demo 大規模影響企業 server。

## OAEP：現代 padding

**Optimal Asymmetric Encryption Padding**，1994 Bellare-Rogaway 提，PKCS#1 v2 後採用。

```
m_padded = 0x00 || maskedSeed || maskedDB
其中 maskedDB = (lHash || PS || 0x01 || m) XOR MGF1(seed)
       seed = 隨機
       maskedSeed = seed XOR MGF1(maskedDB)
```

複雜，但有正式 IND-CCA2 安全證明（在 random oracle model 下）。

```python
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes

private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
public_key = private_key.public_key()

# OAEP encrypt
ciphertext = public_key.encrypt(
    b"Hello, World",
    padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None
    )
)

# OAEP decrypt
plaintext = private_key.decrypt(
    ciphertext,
    padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None
    )
)
```

**RSA 加密一律用 OAEP**。簽章另一套（PSS）下章看。

## RSA-PSS：簽章用 padding

簽章不能直接 `H(m)^d mod n`（缺 randomness、可能 collision）。**PSS (Probabilistic Signature Scheme)**：

```
PSS-SIGN(m, d, n):
  salt = random
  H = SHA256(0x00...0x00 || H(m) || salt)
  PS = padding (zeros)
  DB = PS || 0x01 || salt
  maskedDB = DB XOR MGF1(H)
  EM = maskedDB || H || 0xBC
  s = pow(int(EM), d, n)
```

OAEP 對加密、PSS 對簽章。**現代 RSA 都該用 PSS**，但 legacy 系統仍有大量 PKCS#1 v1.5 簽章。

## Key size 選擇

```
2048-bit RSA：~ 112-bit security（夠 2030）
3072-bit RSA：~ 128-bit security（NIST 推薦 to 2030+）
4096-bit RSA：~ 152-bit security
8192-bit：超慢，特殊場景才用
```

對比：**256-bit ECC ≈ 3072-bit RSA**（安全度等同，計算快很多）。**現代新系統建議用 ECC，舊系統維護 RSA-2048/3072**。

## 一個常見誤解

「RSA 加密的訊息不能比 n 大，怎麼加密大檔案？」

**RSA 不直接加密大檔案**。實務模式：

```
1. 隨機產生 AES key (32 byte)
2. AES 加密整個檔案
3. RSA-OAEP 加密 AES key（這把 < 2048 bit）
4. 送 (RSA-encrypted-key, AES-encrypted-file)
```

這叫 **hybrid encryption / KEM-DEM**。RSA 只負責「加密小的 key」，重活對稱密碼做。**所有 production 公鑰加密都這樣**。

純 RSA 加密 GB 檔案是教學用法，現實沒人做（且非常慢）。

## 自我檢核

- [ ] 我能寫 RSA keygen / encrypt / decrypt（用 `pow()`）
- [ ] 我能用 CRT 加速解密
- [ ] 我能解釋為什麼 textbook RSA 不能用
- [ ] 我能說出 PKCS#1 v1.5 與 OAEP 的差別
- [ ] 我能描述 hybrid encryption (RSA + AES) 模式
- [ ] 我知道 RSA-PSS 是簽章用的 padding

下一章看 RSA 的四大經典攻擊：Wiener、common modulus、Hastad、Bleichenbacher。

→ [Ch 20 RSA 攻擊](./20-rsa-attacks.md)
