# Ch 2 — 數論速覽

> **目標**：掌握 modular arithmetic、Extended Euclidean Algorithm、Euler's totient、CRT、群/環/體的直覺，為 RSA（Ch 19）和 ECC（Ch 22）打下數學基礎。

> 這章的數學比較密集——但每個概念都會給具體數字和 Python/SageMath code。如果你在某個定理卡住，先跑 code 看結果，再回來理解「為什麼」。

## Modular Arithmetic（模運算）

### 基本概念

`a ≡ b (mod n)` 的意思是：a 和 b 除以 n 的餘數相同。等價地：n 整除 (a - b)。

```
17 ≡ 2 (mod 5)    因為 17 = 3×5 + 2
-3 ≡ 4 (mod 7)    因為 -3 = (-1)×7 + 4
```

你可以把 mod 想成時鐘：12 小時制的時鐘是 mod 12，3 點過 15 小時是 6 點（18 mod 12 = 6）。

### 四則運算

加法、減法、乘法在 mod 下封閉：

```
(a + b) mod n = ((a mod n) + (b mod n)) mod n
(a - b) mod n = ((a mod n) - (b mod n)) mod n
(a × b) mod n = ((a mod n) × (b mod n)) mod n
```

Python 驗證：

```python
n = 17

# 加法
assert (23 + 31) % n == ((23 % n) + (31 % n)) % n  # 54 % 17 = 3

# 乘法
assert (23 * 31) % n == ((23 % n) * (31 % n)) % n  # 713 % 17 = 15

# 指數——Python 內建 pow(base, exp, mod) 是高效的 modular exponentiation
assert pow(3, 100, 17) == 1  # Fermat's Little Theorem: 3^16 ≡ 1 (mod 17)
```

`pow(base, exp, mod)` 內部用 square-and-multiply（二進位快速冪），不會真的算出 `3^100` 再取 mod——那個數字有 48 位數。

### 除法？不是除法——是乘以 modular inverse

mod 運算沒有「除法」，但有 **modular inverse（模反元素）**。

`a` 的 modular inverse `a⁻¹ (mod n)` 是滿足 `a × a⁻¹ ≡ 1 (mod n)` 的數。

```
3 的 inverse (mod 17):
3 × 6 = 18 = 1×17 + 1 → 3 × 6 ≡ 1 (mod 17)
所以 3⁻¹ ≡ 6 (mod 17)
```

**Modular inverse 不一定存在**。它存在的充要條件：`gcd(a, n) = 1`（a 和 n 互質）。

```
4 的 inverse (mod 6) 不存在：
4 × 0 = 0, 4 × 1 = 4, 4 × 2 = 8 ≡ 2, 4 × 3 = 12 ≡ 0, 4 × 4 = 16 ≡ 4, 4 × 5 = 20 ≡ 2
沒有一個數乘以 4 mod 6 等於 1。
原因：gcd(4, 6) = 2 ≠ 1
```

找 modular inverse 的方法：Extended Euclidean Algorithm（下一節）。

Python 3.8+ 有內建：

```python
# Python 3.8+
print(pow(3, -1, 17))  # 6

# 等價於
# pow(3, 17 - 2, 17)   # Fermat's Little Theorem (只在 n 是質數時)
```

## Extended Euclidean Algorithm（擴展歐幾里得算法）

### Euclidean Algorithm（歐幾里得算法）

先回顧 GCD 的計算：

```
gcd(252, 105):
252 = 2 × 105 + 42
105 = 2 × 42  + 21
 42 = 2 × 21  + 0
→ gcd = 21
```

Python:

```python
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

print(gcd(252, 105))  # 21
```

### Extended GCD

Extended GCD 不只算 gcd(a, b)，還找到整數 x, y 使得：

```
a × x + b × y = gcd(a, b)
```

這叫 **Bézout's identity**。x, y 是我們要的——當 gcd(a, n) = 1 時，`a × x + n × y = 1`，取 mod n 得 `a × x ≡ 1 (mod n)`，所以 x 就是 a 的 modular inverse。

手算範例——求 3⁻¹ (mod 17)：

```
求 x, y 使得 3x + 17y = 1

Step 1: 歐幾里得除法
17 = 5 × 3 + 2    ... (1)
 3 = 1 × 2 + 1    ... (2)
 2 = 2 × 1 + 0    gcd = 1 ✓

Step 2: 回代
從 (2): 1 = 3 - 1 × 2
把 (1) 的 2 = 17 - 5×3 代入:
1 = 3 - 1 × (17 - 5×3)
1 = 3 - 17 + 5×3
1 = 6×3 - 1×17

所以 x = 6, y = -1
驗證: 3 × 6 + 17 × (-1) = 18 - 17 = 1 ✓
3⁻¹ ≡ 6 (mod 17)
```

Python 實作：

```python
def ext_gcd(a, b):
    """回傳 (gcd, x, y) 使得 a*x + b*y = gcd(a, b)"""
    if b == 0:
        return a, 1, 0
    g, x, y = ext_gcd(b, a % b)
    return g, y, x - (a // b) * y

# 測試
g, x, y = ext_gcd(3, 17)
print(f"gcd={g}, x={x}, y={y}")        # gcd=1, x=6, y=-1
print(f"3*{x} + 17*{y} = {3*x + 17*y}")  # 1
print(f"3^(-1) mod 17 = {x % 17}")       # 6

# 用來求 modular inverse
def mod_inverse(a, n):
    g, x, _ = ext_gcd(a, n)
    if g != 1:
        raise ValueError(f"gcd({a}, {n}) = {g} ≠ 1，inverse 不存在")
    return x % n

print(mod_inverse(3, 17))     # 6
print(mod_inverse(7, 26))     # 15 (會用在仿射密碼)
# mod_inverse(4, 6)           # ValueError: gcd(4, 6) = 2
```

`ext_gcd` 的遞迴深度是 O(log(min(a,b)))——和普通 GCD 一樣快。

## Euler's Totient Function — φ(n)

### 定義

φ(n) = 小於 n 且與 n 互質的正整數個數。

```
φ(1)  = 1     {1}
φ(6)  = 2     {1, 5}           (2,3,4 和 6 不互質)
φ(7)  = 6     {1,2,3,4,5,6}   (7 是質數，所有比它小的數都和它互質)
φ(8)  = 4     {1, 3, 5, 7}
φ(12) = 4     {1, 5, 7, 11}
```

### 計算公式

- **p 是質數**：φ(p) = p - 1
- **p^k**：φ(p^k) = p^k - p^(k-1) = p^(k-1) × (p - 1)
- **mn（m, n 互質）**：φ(mn) = φ(m) × φ(n)

RSA 最重要的特殊情況：**n = p × q（p, q 是不同的質數）**

```
φ(pq) = φ(p) × φ(q) = (p-1)(q-1)
```

這就是 RSA 的核心：n = p × q 公開，但 φ(n) = (p-1)(q-1) 需要知道 p 和 q 才能算。不知道 φ(n) 就不能算出私鑰。

```python
# RSA 的例子
p, q = 61, 53
n = p * q              # 3233
phi_n = (p-1) * (q-1)  # 3120

# 公鑰指數 e（常見選擇 65537）
e = 17                  # 用小的方便演示
assert gcd(e, phi_n) == 1  # e 和 φ(n) 必須互質

# 私鑰指數 d = e^(-1) mod φ(n)
d = mod_inverse(e, phi_n)
print(f"d = {d}")        # 2753

# 加密/解密
m = 65                    # 明文（必須 < n）
c = pow(m, e, n)          # 2790 （密文）
m2 = pow(c, d, n)         # 65   （解密回明文）
print(f"加密: {m} → {c}")
print(f"解密: {c} → {m2}")
assert m == m2
```

**重點**：φ(n) 的計算「容易」——前提是你知道 n 的質因數分解。對 RSA 來說，如果你能分解 n = p × q，你就能算出 φ(n)，進而算出私鑰 d。RSA 的安全性建立在「大整數分解很難」這個假設上。

## Fermat's Little Theorem 和 Euler's Theorem

### Fermat's Little Theorem

如果 p 是質數，a 不是 p 的倍數：

```
a^(p-1) ≡ 1 (mod p)
```

```python
p = 17
for a in [2, 3, 5, 7, 11]:
    print(f"{a}^{p-1} mod {p} = {pow(a, p-1, p)}")
    # 全部是 1
```

推論：`a^(-1) ≡ a^(p-2) (mod p)`——這是另一種算 modular inverse 的方式（只在 mod 質數時有效）。

### Euler's Theorem

Fermat 的推廣——n 不需要是質數：

```
如果 gcd(a, n) = 1，則 a^φ(n) ≡ 1 (mod n)
```

```python
n = 15      # φ(15) = φ(3)×φ(5) = 2×4 = 8
phi_n = 8
for a in [1, 2, 4, 7, 11, 13, 14]:  # 和 15 互質的數
    print(f"{a}^{phi_n} mod {n} = {pow(a, phi_n, n)}")
    # 全部是 1
```

RSA 解密為什麼能還原明文：

```
c = m^e mod n
c^d mod n = m^(ed) mod n

因為 e×d ≡ 1 (mod φ(n))，所以 ed = 1 + k×φ(n)
m^(ed) = m^(1 + k×φ(n)) = m × (m^φ(n))^k ≡ m × 1^k = m (mod n)
```

## Chinese Remainder Theorem（中國剩餘定理）

### 直覺：時鐘拼湊問題

一個數除以 3 餘 2，除以 5 餘 3，除以 7 餘 2。這個數是多少？

```
x ≡ 2 (mod 3)
x ≡ 3 (mod 5)
x ≡ 2 (mod 7)
```

CRT 說：如果模數兩兩互質（3, 5, 7 確實互質），解在 mod (3×5×7 = 105) 下唯一。

答案是 x = 23（你可以驗證：23 = 7×3 + 2, 23 = 4×5 + 3, 23 = 3×7 + 2）。

### Python 實作

```python
def crt(remainders, moduli):
    """中國剩餘定理
    輸入: remainders = [r1, r2, ...], moduli = [m1, m2, ...]
    輸出: x 使得 x ≡ ri (mod mi) 對所有 i
    前提: moduli 兩兩互質
    """
    N = 1
    for m in moduli:
        N *= m

    x = 0
    for ri, mi in zip(remainders, moduli):
        Ni = N // mi                      # 其他所有模數的乘積
        _, xi, _ = ext_gcd(Ni, mi)        # Ni 的 inverse mod mi
        x += ri * Ni * xi

    return x % N

# 測試
result = crt([2, 3, 2], [3, 5, 7])
print(f"x = {result}")  # 23

# 驗證
assert result % 3 == 2
assert result % 5 == 3
assert result % 7 == 2
```

### CRT 在密碼學的應用

**RSA CRT 加速**：RSA 解密 `m = c^d mod n` 中，n = p × q 可能有 2048 bit。直接算 `pow(c, d, n)` 很慢。用 CRT 可以拆成兩個小問題：

```python
# RSA CRT 解密
def rsa_crt_decrypt(c, d, p, q):
    dp = d % (p - 1)    # d mod (p-1)
    dq = d % (q - 1)    # d mod (q-1)
    qinv = mod_inverse(q, p)

    m1 = pow(c, dp, p)  # c^dp mod p （p 只有 1024 bit）
    m2 = pow(c, dq, q)  # c^dq mod q （q 只有 1024 bit）

    h = (qinv * (m1 - m2)) % p
    m = m2 + h * q
    return m

# 對比
p, q = 61, 53
n = p * q      # 3233
e = 17
d = mod_inverse(e, (p-1)*(q-1))  # 2753
c = pow(65, e, n)                 # 2790

m_normal = pow(c, d, n)
m_crt = rsa_crt_decrypt(c, d, p, q)
print(f"normal: {m_normal}, CRT: {m_crt}")  # 都是 65
assert m_normal == m_crt
```

CRT 加速的原因：modular exponentiation 的成本大約和模數的 bit 數的**三次方**成正比。把一個 2048-bit 的運算拆成兩個 1024-bit 的運算，速度提升約 4 倍。實際的 RSA 私鑰檔案裡，`dp`、`dq`、`qinv` 這些 CRT 參數都預先存好。

## 群、環、體的直覺

不要被抽象代數嚇到——你只需要理解三個層次的「數字系統」，每個層次比前一個多一種運算。

### 群（Group）

一個集合 + 一個運算，滿足四個性質：封閉性、結合律、單位元、反元素。

**具體例子**：Z/7Z* = {1, 2, 3, 4, 5, 6} 在乘法 mod 7 下形成群。

```python
# Z/7Z* 乘法群
n = 7
group = [i for i in range(1, n) if gcd(i, n) == 1]
print(f"Z/{n}Z* = {group}")  # [1, 2, 3, 4, 5, 6]

# 每個元素都有 inverse
for a in group:
    inv = pow(a, -1, n)
    print(f"{a}^(-1) = {inv}  (驗證: {a}*{inv} mod {n} = {(a*inv)%n})")
```

群的**階（order）**是元素個數。Z/7Z* 的階 = 6 = φ(7)。

群在密碼學的角色：DH key exchange 和 ECDSA 都建立在群結構上。

### 環（Ring）

一個集合 + 兩個運算（加法和乘法），加法形成群，乘法有結合律和分配律，但乘法的 inverse **不要求存在**。

**具體例子**：Z/nZ = {0, 1, 2, ..., n-1} 在加法和乘法 mod n 下形成環。

```python
# Z/6Z 環
# 加法：0+0=0, 1+5=0, 2+4=0, 3+3=0 — 每個元素都有加法 inverse → 加法群
# 乘法：2 × 3 = 0 (mod 6) — 兩個非零元素乘出零！
# 2 的乘法 inverse 不存在（gcd(2,6)=2≠1）
# 這就是環和體的區別
```

環允許「零因子」（zero divisor）：兩個非零元素乘出零。在 Z/6Z 裡，2 × 3 ≡ 0 (mod 6)。

### 體（Field）

環 + 每個非零元素都有乘法 inverse。也就是說：加法和乘法都能「做除法」。

兩個在密碼學裡最重要的體：

**GF(p) — 質數體**

```python
# GF(17) = Z/17Z
# 因為 17 是質數，每個非零元素都和 17 互質
# 所以每個非零元素都有 modular inverse → 體
p = 17
for a in range(1, p):
    inv = pow(a, -1, p)
    assert (a * inv) % p == 1
print(f"GF({p}) 的所有非零元素都有 inverse ✓")
```

GF(p) 用在 RSA（mod p 的運算）和 DH（mod p 的指數運算）。

**GF(2^8) — AES 的有限體**

這個比較特殊：元素是 8-bit 的多項式，加法是 XOR，乘法是多項式乘法 mod 一個不可約多項式（irreducible polynomial）。

```
GF(2^8) 的元素：0x00 到 0xFF（256 個）
加法：XOR
   0x57 + 0x83 = 0x57 XOR 0x83 = 0xD4
乘法：多項式乘法 mod (x^8 + x^4 + x^3 + x + 1)
   這就是 AES 規格書 FIPS 197 定義的乘法
```

SageMath 驗證：

```python
# 在 SageMath 裡跑
R.<x> = GF(2)[]
F.<a> = GF(2^8, modulus=x^8 + x^4 + x^3 + x + 1)

# 0x57 = x^6 + x^4 + x^2 + x + 1
elem1 = F.fetch_int(0x57)
# 0x83 = x^7 + x + 1
elem2 = F.fetch_int(0x83)

product = elem1 * elem2
print(f"GF(2^8): 0x57 × 0x83 = {hex(product.integer_representation())}")
# 0xC1 — 和 FIPS 197 Section 4.2 的範例一致
```

Ch 9（AES 數學）會深入 GF(2^8) 的每一個細節。這裡只需要記住：AES 的 S-box 和 MixColumns 是在 GF(2^8) 上做的運算，不是普通的整數運算。

### 三者的關係

```
體 ⊂ 環 ⊂ 群（在結構豐富度上）

群：一個運算           例：Z/7Z* 在乘法下
環：兩個運算（加+乘）  例：Z/6Z
體：兩個運算 + 除法    例：GF(17), GF(2^8)

密碼學用到的：
  DH / ECDSA → 群結構（離散對數問題）
  RSA        → 環結構（Z/nZ，n=pq 是合數所以不是體）
  AES        → 體結構（GF(2^8)）
```

## 離散對數問題（Discrete Logarithm Problem, DLP）

### 正向快、反向慢

在群 Z/pZ* 裡：

- **正向**（指數運算）：已知 g, x, p，算 g^x mod p → 用 square-and-multiply，O(log x) 次乘法，很快
- **反向**（離散對數）：已知 g, h, p，求 x 使得 g^x ≡ h (mod p) → 沒有已知的多項式時間演算法

```python
p = 104729   # 一個質數
g = 2        # 生成元

# 正向：快（square-and-multiply，毫秒級）
x = 12345
h = pow(g, x, p)
print(f"g^x mod p = 2^{x} mod {p} = {h}")

# 反向：慢（暴力搜索，小 p 可行，2048-bit 不可能）
for i in range(p):
    if pow(g, i, p) == h:
        print(f"離散對數: log_2({h}) = {i}")
        break
```

DLP 的困難性是 DH key exchange 和 ECDSA 的安全基礎：

- **DH**：Alice 送 g^a，Bob 送 g^b，共享密鑰是 g^(ab)。竊聽者看到 g^a 和 g^b，但算不出 a 或 b（DLP）
- **ECDSA**：私鑰 d，公鑰 Q = d × G（橢圓曲線上的點乘法）。知道 Q 和 G，算不出 d（橢圓曲線上的 DLP，ECDLP）

具體數字的直覺：GF(p) 上 DLP 最好的演算法是 Number Field Sieve，2048-bit 的 p 目前需要的計算量約 2^112。橢圓曲線上的 DLP 目前最好的演算法是 Pollard's rho，256-bit 的曲線需要約 2^128 的計算量——這就是為什麼 ECC 可以用比 RSA 短很多的 key 達到相同安全強度。

## 踩雷集錦

### 1. 「Modular inverse 永遠存在」

不。`gcd(a, n) = 1` 是必要條件。最常犯的場景：RSA 的 e 選錯——e 必須和 φ(n) 互質，否則 d = e^(-1) mod φ(n) 不存在。65537 是常見的 e 值，因為它是質數且很少和 φ(n) 有公因子。

### 2. 「大數運算很慢」

在 Python 裡不成立——Python 的 int 是 arbitrary precision，底層用類似 GMP 的算法。`pow(base, exp, mod)` 在 Python 裡即使 base、exp、mod 是 2048-bit 的數也能在毫秒內完成。

但在 C 裡，你需要 GMP（GNU Multiple Precision Arithmetic Library）或 OpenSSL 的 BIGNUM——`unsigned long long` 只有 64 bit，遠不夠 RSA 的 2048 bit。Python 的 `pow(g, x, p)` 即使三個參數都是 2048-bit 也能在幾毫秒內完成。

### 3. φ(n) 容易算——前提是你知道質因數分解

φ(3233) = φ(61 × 53) = 60 × 52 = 3120——如果你知道 n = 61 × 53。

但如果 n 有 2048 bit，你不知道 p 和 q，計算 φ(n) 的難度等同於分解 n。這正是 RSA 的安全性來源：公鑰 (n, e) 裡的 n 是公開的，但只有知道 p, q 才能算 φ(n)，才能算出私鑰 d。

## 本章重點整理

- Modular arithmetic：加減乘直接 mod，「除法」= 乘以 modular inverse
- Extended GCD：找 x, y 使得 ax + by = gcd(a, b)——用來算 modular inverse
- Euler's totient：φ(pq) = (p-1)(q-1)——RSA 的核心
- Fermat / Euler 定理：a^φ(n) ≡ 1 (mod n)——RSA 解密的數學基礎
- CRT：把 mod n 的問題拆成 mod p 和 mod q——RSA CRT 加速 4 倍
- 群/環/體：DH 用群、RSA 用環、AES 用體
- DLP：正向快（指數）、反向慢（離散對數）——DH 和 ECDSA 的安全基礎

## 自我檢核

- [ ] 能手算 ext_gcd(35, 15) 並說明為什麼 35^(-1) mod 15 不存在
- [ ] 能解釋 φ(n) 在 RSA 裡的角色——為什麼知道 φ(n) 就能算出私鑰
- [ ] 能用 CRT 解一個三元聯立同餘式
- [ ] 能區分群、環、體，並各舉一個密碼學應用
- [ ] 能解釋為什麼 ECC 的 key 比 RSA 短——同樣安全強度下，ECDLP 比整數 DLP 更難

## 延伸閱讀

### 書籍

- **《A Graduate Course in Applied Cryptography》— Ch 8–10** — Boneh & Shoup
  - **讀哪裡**：Ch 8（arithmetic of integers）、Ch 9（groups）、Ch 10（public-key tools）
  - **學什麼**：本章所有概念的嚴格定義和完整證明；本章講直覺，Boneh 講形式化
  - **前提**：本章的內容

- **《Serious Cryptography》— Ch 11** — Aumasson
  - **讀哪裡**：Ch 11（RSA）的數學背景部分
  - **學什麼**：RSA 用到的數論，寫法更偏工程、比 Boneh 好入門
  - **前提**：本章的 modular arithmetic

### SageMath 實驗

- **[SageMath: Number Theory](https://doc.sagemath.org/html/en/reference/rings_standard/sage/rings/finite_rings/integer_mod.html)**
  - **讀哪裡**：IntegerModRing 的 API 文件
  - **學什麼**：怎麼用 SageMath 做本章所有運算（比 Python 原生更方便）
  - **前提**：SageMath 基礎（Ch 0）

→ [Ch 3 機率與資訊論速覽](./03-probability-info-theory.md)
