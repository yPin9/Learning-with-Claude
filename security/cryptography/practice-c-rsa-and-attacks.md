# 練習 C — RSA 手刻 + 三大經典攻擊

> 目標：手刻 RSA-2048 key pair + 跑 Wiener / Hastad / 簡化版 Bleichenbacher 三個經典攻擊。

---

## 概覽

本練習分四個 Phase：

| Phase | 內容 | 預計時間 | 難度 |
|---|---|---|---|
| Phase 1 | 手刻 RSA-2048（自己做 prime generation） | 90 min | ★★★ |
| Phase 2 | Wiener attack（連分數逼近） | 60 min | ★★★ |
| Phase 3 | Hastad broadcast attack（CRT + 開根號） | 45 min | ★★☆ |
| Phase 4 | 簡化版 Bleichenbacher（optional） | 60 min | ★★★★ |

環境：Python 3.11, Ubuntu 22.04。不允許使用 `pycryptodome`、`cryptography` 等套件的 RSA keygen——全部手刻。

---

## Phase 1：手刻 RSA-2048

### 目標

從零開始生成一對 RSA-2048 key pair，包含：
1. 質數生成（Miller-Rabin 測試）
2. Key generation（n, e, d, CRT 參數）
3. 加密 / 解密
4. CRT 加速解密

### 步驟

#### Step 1.1：Miller-Rabin 質數測試

```python
"""
實作 Miller-Rabin probabilistic primality test。
要求：至少 20 rounds，確保 false positive 機率 < 4^(-20)。
"""
import secrets


def miller_rabin(n, rounds=20):
    """
    回傳 True = probably prime, False = definitely composite
    你要實作：
    1. 處理 n < 4 的 edge case
    2. 寫出 n-1 = 2^r × d（d 為奇數）
    3. 對每個 witness a，執行 Miller-Rabin 測試
    """
    # === 你的 code ===
    pass


# 測試
assert miller_rabin(2) == True
assert miller_rabin(3) == True
assert miller_rabin(4) == False
assert miller_rabin(561) == False   # Carmichael number（騙 Fermat，但騙不了 MR）
assert miller_rabin(7919) == True
assert miller_rabin(104729) == True
print("Miller-Rabin tests passed ✓")
```

**提示**：
- n-1 = 2^r × d 的分解：不斷除以 2 直到奇數
- Witness a 的測試：計算 x = a^d mod n，然後做 r 次 squaring
- 如果在任何 squaring 步驟中 x 變成 n-1，這個 witness 不能判定 composite

#### Step 1.2：質數生成

```python
def generate_prime(bits):
    """
    生成一個 bits 位元的質數。
    要求：
    1. 最高位必須是 1（確保 bit 數正確）
    2. 最低位必須是 1（偶數一定不是質數）
    3. 用 secrets 模組生成隨機數（不要用 random）
    """
    # === 你的 code ===
    pass


# 測試
p = generate_prime(1024)
assert p.bit_length() == 1024
assert miller_rabin(p)
print(f"Generated 1024-bit prime: {hex(p)[:30]}...")
```

**提示**：
- 一個隨機奇數是質數的機率約為 2/(bits × ln2)
- 對 1024-bit，平均嘗試 ~355 次
- 可以先過濾掉小質數（2, 3, 5, 7, 11, ...）的倍數，加速

#### Step 1.3：Extended Euclidean Algorithm

```python
def extended_gcd(a, b):
    """
    回傳 (gcd, x, y) 使得 a*x + b*y = gcd(a, b)
    """
    # === 你的 code ===
    pass


def mod_inverse(a, m):
    """
    計算 a 的模逆元：a^(-1) mod m
    如果 gcd(a, m) != 1，raise ValueError
    """
    # === 你的 code ===
    pass


# 測試
g, x, y = extended_gcd(65537, 3120)
assert g == 1
assert (65537 * x + 3120 * y) == 1
print("Extended GCD tests passed ✓")
```

#### Step 1.4：RSA Key Generation

```python
def rsa_keygen(bits=2048):
    """
    生成 RSA key pair。
    回傳 dict 包含：
      n, e, d, p, q, d_p, d_q, q_inv

    要求：
    1. |p - q| 要夠大（> 2^(bits/2 - 100)）
    2. n.bit_length() == bits
    3. e = 65537
    4. 包含 CRT 參數
    """
    # === 你的 code ===
    pass


# 測試
key = rsa_keygen(2048)
assert key['n'].bit_length() == 2048
assert key['e'] == 65537
assert (key['e'] * key['d']) % ((key['p']-1) * (key['q']-1)) == 1
assert key['p'] * key['q'] == key['n']
print("RSA keygen tests passed ✓")
```

#### Step 1.5：加密、解密、CRT 解密

```python
def rsa_encrypt(m, n, e):
    """Textbook RSA 加密"""
    assert 0 <= m < n
    return pow(m, e, n)


def rsa_decrypt(c, n, d):
    """Textbook RSA 解密"""
    return pow(c, d, n)


def rsa_decrypt_crt(c, p, q, d_p, d_q, q_inv):
    """
    用 CRT 加速 RSA 解密。
    要求：
    1. m1 = c^d_p mod p
    2. m2 = c^d_q mod q
    3. h = q_inv × (m1 - m2) mod p
    4. m = m2 + h × q
    """
    # === 你的 code ===
    pass


# 測試
key = rsa_keygen(2048)
m = int.from_bytes(b"RSA works!", 'big')
c = rsa_encrypt(m, key['n'], key['e'])
m1 = rsa_decrypt(c, key['n'], key['d'])
m2 = rsa_decrypt_crt(c, key['p'], key['q'],
                      key['d_p'], key['d_q'], key['q_inv'])
assert m1 == m
assert m2 == m
print("Encrypt/Decrypt tests passed ✓")

# 性能比較
import time
count = 50
start = time.perf_counter()
for _ in range(count):
    rsa_decrypt(c, key['n'], key['d'])
t_normal = time.perf_counter() - start

start = time.perf_counter()
for _ in range(count):
    rsa_decrypt_crt(c, key['p'], key['q'],
                    key['d_p'], key['d_q'], key['q_inv'])
t_crt = time.perf_counter() - start

print(f"Normal decrypt: {t_normal/count*1000:.1f} ms/op")
print(f"CRT decrypt:    {t_crt/count*1000:.1f} ms/op")
print(f"Speedup:        {t_normal/t_crt:.1f}x")
```

**Phase 1 驗收標準**：
- [ ] Miller-Rabin 正確判定（含 Carmichael number）
- [ ] 生成的 prime 確實是 prime
- [ ] RSA-2048 key pair 通過正確性測試
- [ ] CRT 解密速度明顯快於普通解密（預期 3-4x）
- [ ] 整個 keygen 在 10 秒內完成

---

## Phase 2：Wiener Attack

### 目標

實作 Wiener attack，對 d < n^(1/4)/3 的 RSA key 恢復私鑰。

### 步驟

#### Step 2.1：連分數展開

```python
def continued_fraction_expansion(numerator, denominator):
    """
    計算 numerator/denominator 的連分數展開 [a0; a1, a2, ...]
    回傳 list of integers
    """
    # === 你的 code ===
    pass


# 測試
assert continued_fraction_expansion(17993, 90581) == [0, 5, 29, 4, 1, 3, 2, 4, 3]
# 或類似的展開（取決於實作細節）
print("Continued fraction tests passed ✓")
```

#### Step 2.2：收斂子（Convergents）

```python
def convergents_from_cf(cf):
    """
    從連分數展開計算所有收斂子 p_i/q_i
    回傳 list of (p_i, q_i) tuples

    遞推公式：
      h[-1] = 1, h[-2] = 0
      k[-1] = 0, k[-2] = 1
      h[i] = a[i] * h[i-1] + h[i-2]
      k[i] = a[i] * k[i-1] + k[i-2]
    """
    # === 你的 code ===
    pass


# 測試
cf = [3, 7, 15, 1]  # π ≈ [3; 7, 15, 1]
convs = convergents_from_cf(cf)
# 收斂子應該接近 π：3/1, 22/7, 333/106, 355/113
print(f"Convergents of [3;7,15,1]: {convs}")
```

#### Step 2.3：Wiener Attack

```python
from math import isqrt


def wiener_attack(e, n):
    """
    嘗試用 Wiener attack 恢復 d。
    成功回傳 (d, p, q)，失敗回傳 None。

    策略：
    1. 計算 e/n 的連分數展開
    2. 對每個收斂子 k/d：
       a. 檢查 (ed - 1) 是否被 k 整除
       b. 如果是，計算 φ(n) = (ed - 1) / k
       c. 用 φ(n) 和 n 解出 p, q
       d. 驗證 p × q == n
    """
    # === 你的 code ===
    pass


# 測試：構造一個弱 RSA key
def make_weak_rsa(bits=1024):
    """
    生成一個 d 很小的 RSA key（容易被 Wiener attack 打的）
    """
    half = bits // 2
    p = generate_prime(half)
    q = generate_prime(half)
    n = p * q
    phi = (p - 1) * (q - 1)

    # 選一個很小的 d
    bound = isqrt(isqrt(n)) // 3
    from math import gcd
    d = secrets.randbelow(bound - 2) + 2
    while gcd(d, phi) != 1:
        d = secrets.randbelow(bound - 2) + 2

    e = mod_inverse(d, phi)
    return n, e, d, p, q


n, e, d_real, p_real, q_real = make_weak_rsa(1024)
print(f"n: {n.bit_length()} bits")
print(f"e: {e.bit_length()} bits (unusually large)")
print(f"d: {d_real.bit_length()} bits (dangerously small)")

result = wiener_attack(e, n)
if result:
    d_found, p_found, q_found = result
    assert d_found == d_real
    print(f"\nWiener attack 成功！")
    print(f"d = {d_found}")
    print(f"p = {p_found}")
    print(f"q = {q_found}")
else:
    print("Wiener attack 失敗")
```

**Phase 2 驗收標準**：
- [ ] 連分數展開正確
- [ ] 收斂子計算正確
- [ ] 對 1024-bit weak RSA key，Wiener attack 在 < 1 秒內恢復 d
- [ ] 驗證：用恢復的 d 可以解密密文

---

## Phase 3：Hastad Broadcast Attack

### 目標

實作 Hastad broadcast attack，對 e=3 + 同一明文發給 3 人的情形恢復明文。

### 步驟

#### Step 3.1：CRT（中國剩餘定理）

```python
def chinese_remainder_theorem(remainders, moduli):
    """
    解聯立同餘方程：
      x ≡ r_i (mod m_i) for all i
    回傳 x mod (m_1 × m_2 × ... × m_k)

    要求：moduli 兩兩互質
    """
    # === 你的 code ===
    pass


# 測試
# x ≡ 2 (mod 3), x ≡ 3 (mod 5), x ≡ 2 (mod 7)
x = chinese_remainder_theorem([2, 3, 2], [3, 5, 7])
assert x % 3 == 2
assert x % 5 == 3
assert x % 7 == 2
print(f"CRT result: {x} (mod 105)")
print("CRT tests passed ✓")
```

#### Step 3.2：整數 k 次根

```python
def integer_kth_root(n, k):
    """
    計算 n 的整數 k 次根。
    如果 n 不是完全 k 次方，回傳 None。
    用 Newton's method。
    """
    # === 你的 code ===
    pass


# 測試
assert integer_kth_root(27, 3) == 3
assert integer_kth_root(1000, 3) == 10
assert integer_kth_root(26, 3) == None
assert integer_kth_root(2**300, 3) == 2**100  # 大數測試
print("Integer kth root tests passed ✓")
```

#### Step 3.3：Hastad Broadcast Attack

```python
def hastad_broadcast_attack(ciphertexts, moduli, e=3):
    """
    Hastad broadcast attack。
    ciphertexts: [c1, c2, c3]
    moduli: [n1, n2, n3]
    e: 公鑰指數（預設 3）

    策略：
    1. 用 CRT 恢復 m^e mod (n1 × n2 × n3)
    2. 因為 m < min(n_i)，所以 m^e < n1 × n2 × n3
    3. m^e 是一個「普通整數」，直接開 e 次根
    """
    # === 你的 code ===
    pass


# 測試：生成 3 組 e=3 的 RSA key
def make_rsa_e3(bits=1024):
    """生成 e=3 的 RSA key"""
    half = bits // 2
    while True:
        p = generate_prime(half)
        q = generate_prime(half)
        n = p * q
        phi = (p - 1) * (q - 1)
        from math import gcd
        if gcd(3, phi) == 1 and n.bit_length() == bits:
            d = mod_inverse(3, phi)
            return n, 3, d


n1, _, _ = make_rsa_e3(1024)
n2, _, _ = make_rsa_e3(1024)
n3, _, _ = make_rsa_e3(1024)

# 明文（必須 < min(n1, n2, n3)）
m = int.from_bytes(b"Hastad attack works!", 'big')
print(f"原始明文: {m}")

# 三個密文
c1 = pow(m, 3, n1)
c2 = pow(m, 3, n2)
c3 = pow(m, 3, n3)

# 攻擊
m_recovered = hastad_broadcast_attack([c1, c2, c3], [n1, n2, n3], e=3)

if m_recovered is not None:
    print(f"恢復的明文: {m_recovered}")
    print(f"轉回 bytes: {m_recovered.to_bytes((m_recovered.bit_length()+7)//8, 'big')}")
    assert m_recovered == m
    print("Hastad broadcast attack 成功 ✓")
else:
    print("攻擊失敗")
```

**Phase 3 驗收標準**：
- [ ] CRT 正確（小數和大數都測試）
- [ ] 整數 k 次根正確（包含 2^300 級別的大數）
- [ ] 對 1024-bit RSA key + e=3，Hastad attack 成功恢復明文
- [ ] 攻擊時間 < 1 秒

---

## Phase 4：簡化版 Bleichenbacher（Optional）

### 目標

實作 Bleichenbacher padding oracle attack 的核心邏輯。因為完整版需要 ~2^20 次 oracle 查詢，這裡用小參數（RSA-256）演示。

### 步驟

#### Step 4.1：PKCS#1 v1.5 Padding Oracle Server

```python
class PaddingOracleServer:
    """
    模擬一個有 PKCS#1 v1.5 padding oracle 的 RSA server。
    oracle 洩露「padding 是否合法」的資訊。
    """

    def __init__(self, bits=256):
        """
        生成小的 RSA key（方便攻擊在合理時間內完成）
        """
        key = rsa_keygen(bits)
        self.n = key['n']
        self.e = key['e']
        self.d = key['d']
        self.k = (self.n.bit_length() + 7) // 8  # key byte length
        self.oracle_calls = 0

    def encrypt(self, plaintext_bytes):
        """加密（帶 PKCS#1 v1.5 padding）"""
        mlen = len(plaintext_bytes)
        ps_len = self.k - mlen - 3
        assert ps_len >= 8, "Message too long"

        # 生成非零隨機 padding
        ps = bytes(secrets.choice(range(1, 256)) for _ in range(ps_len))
        em = b'\x00\x02' + ps + b'\x00' + plaintext_bytes

        m_int = int.from_bytes(em, 'big')
        return rsa_encrypt(m_int, self.n, self.e)

    def oracle(self, ciphertext):
        """
        Padding oracle：解密並回傳 padding 是否合法。
        這就是 Bleichenbacher 利用的 leak。
        """
        self.oracle_calls += 1
        m_int = rsa_decrypt(ciphertext, self.n, self.d)
        em = m_int.to_bytes(self.k, 'big')
        return em[0] == 0x00 and em[1] == 0x02
```

#### Step 4.2：Bleichenbacher 攻擊核心

```python
def bleichenbacher_attack(server, ciphertext):
    """
    Bleichenbacher padding oracle attack（簡化版）。

    核心邏輯：
    1. 利用 RSA 的 multiplicative homomorphism：
       c' = c × s^e mod n → 解密得到 m × s mod n
    2. 如果 oracle(c') = True，知道 m × s mod n ∈ [2B, 3B)
    3. 逐步縮小 m 的範圍

    因為是簡化版，這裡用 brute force 搜索 s。
    完整版有更聰明的搜索策略（見 Bleichenbacher 原論文）。

    你的任務：
    1. 計算 B = 2^(8*(k-2))
    2. 搜索 s 使得 oracle(c × s^e mod n) = True
    3. 用每個有效的 s 縮小 m 的範圍
    """
    n = server.n
    e = server.e
    k = server.k
    B = 1 << (8 * (k - 2))

    print(f"B = 2^{8*(k-2)}")
    print(f"合法 padding 範圍: [{2*B}, {3*B-1}]")

    # Step 1: 找第一個 s1 使得 oracle(c * s1^e) = True
    # === 你的 code ===

    # Step 2: 縮小 m 的範圍
    # === 你的 code ===

    # Step 3: 重複直到範圍只有一個值
    # === 你的 code ===

    print(f"Oracle 查詢次數: {server.oracle_calls}")
    return None  # 替換成恢復的 m


# 測試（用小 RSA key）
server = PaddingOracleServer(bits=256)  # 非常小，僅供演示
plaintext = b"\x42"  # 1 byte 明文
ciphertext = server.encrypt(plaintext)

print(f"RSA key size: {server.n.bit_length()} bits")
print(f"密文: {ciphertext}")
print()

# m_recovered = bleichenbacher_attack(server, ciphertext)
# if m_recovered is not None:
#     recovered_bytes = m_recovered.to_bytes(server.k, 'big')
#     # 找到 0x00 分隔符
#     sep = recovered_bytes.index(b'\x00', 2)
#     plaintext_recovered = recovered_bytes[sep+1:]
#     print(f"恢復的明文: {plaintext_recovered}")
```

**Phase 4 驗收標準**：
- [ ] Padding oracle server 正確實作 PKCS#1 v1.5 格式
- [ ] 攻擊能在小 RSA key（256-bit）上成功（可能需要數分鐘）
- [ ] 理解 Bleichenbacher 的核心邏輯：multiplicative homomorphism + oracle feedback

---

## 完成後的自我檢核

### Phase 1
- [ ] 我能解釋 Miller-Rabin 為什麼是 probabilistic（而非 deterministic）
- [ ] 我能解釋 CRT 加速的數學原理（為什麼快 4 倍）
- [ ] 我的 prime generation 使用 `secrets`（不是 `random`）

### Phase 2
- [ ] 我能手算一個小例子的連分數展開和收斂子
- [ ] 我能解釋 Wiener attack 為什麼在 d > n^0.292 時失效

### Phase 3
- [ ] 我能解釋 CRT 在 Hastad attack 中的角色
- [ ] 我能解釋 OAEP 為什麼可以防禦 Hastad attack

### Phase 4（Optional）
- [ ] 我能畫出 Bleichenbacher attack 的流程圖
- [ ] 我能解釋 RSA 的 multiplicative homomorphism 為什麼是攻擊的核心

---

## 加分挑戰

1. **Common Modulus Attack**：實作 Ch 20 的 common modulus attack，當兩人共用 n 時恢復明文。

2. **Fermat Factorization**：實作 Fermat factorization，對 |p-q| 很小的 RSA key 分解 n。測量不同 |p-q| 下的攻擊時間。

3. **Timing Attack 觀察**：用 `timeit` 測量不同 d 值的 `pow(c, d, n)` 時間。觀察 d 的 hamming weight 和解密時間的關聯。

4. **Boneh-Durfee bound**：在 SageMath 中用 Coppersmith's method 攻擊 d < n^0.292 的 RSA key（超出 Wiener 的 n^0.25 bound）。

---

## 提示與除錯

### 常見問題

**Q: Miller-Rabin 把 Carmichael number 判成質數了**
A: 檢查你的 witness 測試邏輯。Carmichael number（如 561 = 3 × 11 × 17）會通過 Fermat test，但 Miller-Rabin 有額外的 squaring 檢查可以抓到它。

**Q: RSA keygen 很慢**
A: 在生成隨機數前先做小質數篩選（trial division by first 100 primes），可以排除掉 ~75% 的合數。

**Q: Wiener attack 找不到 d**
A: 確認 d 確實 < n^(1/4)/3。用 `isqrt(isqrt(n)) // 3` 計算 bound。

**Q: 整數開三次根不精確**
A: Newton's method 的收斂判斷要用整數比較（不要用浮點數）。最後一步要驗證 `result ** k == n`。

**Q: CRT 的結果溢出**
A: Python 的大整數沒有溢出問題。但要注意負數的 mod 運算：Python 的 `%` 永遠回傳非負值，這是正確的。

---

## 參考答案結構

```
practice-c/
├── phase1_rsa.py          # RSA-2048 手刻
├── phase2_wiener.py       # Wiener attack
├── phase3_hastad.py       # Hastad broadcast attack
├── phase4_bleichenbacher.py  # (Optional)
└── test_all.py            # 整合測試
```

每個 phase 完成後執行 `python test_all.py` 確認所有測試通過。
