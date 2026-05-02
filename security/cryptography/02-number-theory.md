# Ch 2 — 數論速覽

> 目標：把後面 RSA / DH / ECC 都會用到的數論基本功一次補齊：modular arithmetic、擴展 Euclidean、CRT、群/環/體直覺。深入細節等用到再展開。這章是工具箱，不是論文。

## 為什麼密碼學那麼愛數論

簡單答案：**整數的乘法可逆，但「modular 反運算」是難題**。這個非對稱性 = 公鑰密碼的種子。

具體例：
- 算 `3 × 5 mod 7 = 15 mod 7 = 1` 很快
- 反過來「找一個 x 使 `3 × x ≡ 1 (mod 7)`」 — 需要解方程式。N 很小時還能枚舉；N = 2048 bit 時，只有理論方法（ext-Euclid）能做。
- 這個運算還能在 **沒有 inverse 表的世界裡**做（純數論）。

整門密碼學就站在「乘法快、有些反運算難」這個 asymmetry。

## 整除與 GCD

```
a 整除 b：a | b 表示存在整數 k，b = a × k
GCD(a, b)：a 與 b 的最大公因數
```

GCD 用 Euclidean algorithm 算，幾千年前發明、至今仍是最佳：

```python
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

print(gcd(48, 18))   # 6
print(gcd(2024, 1066))  # 2
```

直覺：`gcd(a, b) = gcd(b, a mod b)`，反覆做直到 b = 0。

時間複雜度：**O(log min(a, b))** — 即使 a, b 是 2048-bit 數，只要幾百次 mod 就完。

## Modular Arithmetic：時鐘算術

```
整數 mod n 的世界：取 n 為週期
  如 n = 12（時鐘）：
    7 + 8 ≡ 15 ≡ 3 (mod 12)
    9 × 5 ≡ 45 ≡ 9 (mod 12)
```

正式記法：`a ≡ b (mod n)` 表示 `n | (a - b)`，讀「a 同餘 b mod n」。

幾條基本性質：

```
(a + b) mod n = ((a mod n) + (b mod n)) mod n
(a × b) mod n = ((a mod n) × (b mod n)) mod n
(a - b) mod n = ((a mod n) - (b mod n) + n) mod n
```

**注意減法的 + n** — 程式語言對負數 mod 行為不一致（C 的 `-5 % 3` 在不同編譯器可能是 -2 或 1），自己加 n 確保非負。

Python：`(-5) % 3` = 1（Python 對 mod 是 mathematical mod，回傳非負）。但寫 C 的時候要小心。

## Modular Inverse：核心難題

`a × x ≡ 1 (mod n)` 的解 `x` 叫 a 在 mod n 下的 **inverse**，記 `a⁻¹`。

存在條件：**`gcd(a, n) = 1`**（兩者互質）。

例：

```
3 mod 7 的 inverse 是 5，因為 3 × 5 = 15 ≡ 1 (mod 7)
4 mod 6 沒有 inverse，因為 gcd(4, 6) = 2 ≠ 1
```

怎麼算？小 n 用枚舉，大 n 用 **擴展 Euclidean algorithm**。

## 擴展 Euclidean：算 Bezout 係數

普通 Euclidean 算 `gcd(a, b)`，擴展版同時算出 Bezout 係數 `(x, y)` 使：

```
a × x + b × y = gcd(a, b)
```

如果 `gcd(a, n) = 1`，那 `a × x + n × y = 1`，**取 mod n** 就有 `a × x ≡ 1 (mod n)` — `x` 就是 inverse！

實作：

```python
def ext_gcd(a, b):
    """回傳 (gcd, x, y) 使 a*x + b*y = gcd"""
    if b == 0:
        return a, 1, 0
    g, x1, y1 = ext_gcd(b, a % b)
    return g, y1, x1 - (a // b) * y1

def modinv(a, n):
    g, x, _ = ext_gcd(a, n)
    if g != 1:
        raise ValueError(f"{a} has no inverse mod {n}")
    return x % n

print(modinv(3, 7))    # 5
print(modinv(17, 3120))  # 2753 — RSA 例子
```

**這個函式 RSA 私鑰生成必用**。

## 快速冪：modular exponentiation

`a^b mod n` — RSA 加密就是這個。直接 `pow(a, b)` 再 mod 不行，b = 2048 bit 時 a^b 是天文數字。

正確做法：**square-and-multiply**：

```python
def modpow(a, b, n):
    result = 1
    a = a % n
    while b > 0:
        if b & 1:
            result = (result * a) % n
        a = (a * a) % n
        b >>= 1
    return result
```

複雜度：O(log b) 次乘法 + O(log b) 次 mod。對 2048-bit b，約 2000 次 — 毫秒內完成。

Python 內建 `pow(a, b, n)` 就是這個。**用內建，自寫只用來理解**。

## CRT：中國餘數定理

當你要解一組同餘方程：

```
x ≡ r1 (mod n1)
x ≡ r2 (mod n2)
...
x ≡ rk (mod nk)
```

如果所有 `ni` 兩兩互質，CRT 保證有唯一解 mod `n1 × n2 × ... × nk`。

**密碼學用途**：RSA 解密加速 4×（用 p, q 各自 mod 算再合併，比 mod n 直接算快）。

```python
def crt(rs, ns):
    """解 x ≡ rs[i] (mod ns[i]) 的最小正整數 x"""
    from math import prod
    N = prod(ns)
    x = 0
    for r, n in zip(rs, ns):
        Ni = N // n
        x += r * Ni * modinv(Ni, n)
    return x % N

# x ≡ 2 (mod 3), x ≡ 3 (mod 5), x ≡ 2 (mod 7)
print(crt([2, 3, 2], [3, 5, 7]))   # 23
```

## 質數與費馬小定理

```
費馬小定理：若 p 是質數，gcd(a, p) = 1，則
  a^(p-1) ≡ 1 (mod p)
```

對應 Euler 推廣：

```
Euler 定理：若 gcd(a, n) = 1，則
  a^φ(n) ≡ 1 (mod n)
其中 φ(n) 是 Euler totient（小於 n 且與 n 互質的數量）。
```

`φ(p) = p - 1`（質數）；`φ(pq) = (p-1)(q-1)`（兩質數積，**這就是 RSA**）。

費馬小定理是 RSA 正確性證明的核心 — Ch 19 會展開。

## 質數判定：Miller-Rabin

要找一個大質數（如 1024-bit RSA 用），不能 trial division（太慢）。

**Miller-Rabin** 是機率質性測試：

- 給定 n，做 k 輪測試
- 每輪選一個隨機 `a`，做幾次特定運算
- 若任一輪「失敗」 → n 不是質數（確定）
- 全部通過 → n 是質數的機率 ≥ 1 - 4⁻ᵏ

k = 40 時，誤判機率 < 2⁻⁸⁰，實務上接受。

```python
import random

def miller_rabin(n, k=40):
    if n < 2: return False
    if n in (2, 3): return True
    if n % 2 == 0: return False
    # n - 1 = d × 2^r
    r, d = 0, n - 1
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(k):
        a = random.randint(2, n - 2)
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True

# 找一個 1024-bit 質數
def gen_prime(bits):
    while True:
        n = random.getrandbits(bits) | 1 | (1 << (bits - 1))
        if miller_rabin(n):
            return n
```

Python 內建 `sympy.isprime()` 用更精緻的 Baillie-PSW 測試。**Production RSA keygen 都用某種 Miller-Rabin 變體**。

## 群、環、體：直覺版

抽象代數的三個結構，用密碼學需要的最小 set 講：

### 群 (Group)

集合 G + 一個運算（記為 `·`），滿足：
1. **封閉**：a, b ∈ G → a · b ∈ G
2. **結合律**：(a · b) · c = a · (b · c)
3. **單位元**：存在 e 使 a · e = e · a = a
4. **逆元**：每個 a 都有 a⁻¹ 使 a · a⁻¹ = e

例：
- 整數 + 加法 → 群（單位元 0、逆元 -a）
- `Z*_p`（mod p 下與 p 互質的數）+ 乘法 → 群（**RSA 與 DH 用這個**）
- 橢圓曲線上的點 + point addition → 群（**ECC 用這個**）

**循環群**：群中存在 g 使每個元素都能寫成 g 的某個次方。`Z*_p` 是循環群（有 generator）。

### 環 (Ring)

集合 R + 兩個運算（+ 與 ×），滿足：
1. R 對 + 是阿貝爾群（commutative）
2. × 滿足結合律
3. 分配律：a × (b + c) = a × b + a × c

例：
- 整數 Z 是環
- `Z_n`（mod n 下的數）是環
- 多項式環 `Z[x]` — **AES 與 lattice 都用**

### 體 (Field)

環 + 「除了 0 之外每個元素都有乘法逆元」。

例：
- 有理數 Q、實數 R 是體
- `Z_p`（**p 是質數時**）是體 — RSA / DH 工作的地方
- `GF(2⁸)` — **AES 用這個**，下章會展開
- 橢圓曲線的 base field 是 `GF(p)` 或 `GF(2ⁿ)`

**密碼學常見的「域」**：

| 域 | 特性 | 用途 |
|---|---|---|
| `Z_p` (p 質數) | 整數 mod 質數 | RSA、DH、Schnorr |
| `Z_n` (n 合數) | 整數 mod 合數 | RSA modulus（不是 field，是 ring） |
| `GF(2⁸)` | 多項式 mod 不可約多項式 | AES SubBytes / MixColumns |
| `GF(p^k)` | 一般有限體 | 某些 ECC、PQ |

別被「群、環、體」嚇到。**90% 的密碼學課用 `Z_p` 與 `GF(2⁸)`**，其餘是 specialty。

## 動手練習：自己算

```python
# 1. 算 7^100 mod 13
print(pow(7, 100, 13))   # 9

# 2. 找 17 mod 31 的 inverse
print(modinv(17, 31))    # 11，驗證：17 × 11 = 187 = 6×31+1

# 3. CRT 解
# x ≡ 1 (mod 5), x ≡ 2 (mod 7), x ≡ 3 (mod 11)
print(crt([1, 2, 3], [5, 7, 11]))   # 366

# 4. 生個 256-bit 質數（簡單版）
import random
def quick_prime(bits=256):
    from sympy import isprime
    while True:
        n = random.getrandbits(bits) | 1 | (1 << (bits-1))
        if isprime(n):
            return n
print(hex(quick_prime()))
```

跑過一輪有手感很重要。後面 RSA / DH / ECDSA 章節都假設你能寫這些。

## 一個常見誤解

「`pow(a, b, n)` 真的有那麼快嗎？」

是的，**Python 內建 `pow` 用 GMP 級別的 modular exponentiation**，2048-bit 大概 1ms。但**自己寫 Python loop modpow** 比 `pow()` 慢 10-100×（Python 解譯 overhead）。**寫 RSA / DH 永遠用內建 `pow(a, b, n)`**，自己刻只用來理解算法。

C 端類似：用 GMP（`mpz_powm`）或 OpenSSL（`BN_mod_exp`）— 別自己 roll。

## 自我檢核

- [ ] 我能寫出 ext_gcd 並用它算 modular inverse
- [ ] 我能用 `pow(a, b, n)` 算 modular exponentiation
- [ ] 我能解釋 CRT 是什麼以及它為什麼讓 RSA 解密快 4×
- [ ] 我能說出費馬小定理、Euler 定理的內容
- [ ] 我能寫出 Miller-Rabin 質性測試骨架
- [ ] 我能用直覺說明群 / 環 / 體的差別

下一章補另一根支柱：機率與資訊論。Shannon entropy、unicity distance、PRG / PRF / PRP、IND-CPA。

→ [Ch 3 機率與資訊論速覽](./03-probability-info-theory.md)
