# Ch 25 — 數學與數論

> 目標:面試會考的數學題庫——質數篩、GCD、快速冪、組合數。不要把它當高中數學,是演算法工具。

## GCD / LCM

```python
import math
math.gcd(a, b)    # O(log min(a,b))
math.lcm(a, b)    # 3.9+
```

**輾轉相除法**:

```python
def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

def lcm(a, b):
    return a * b // gcd(a, b)
```

**注意溢位**:`a * b` 可能很大。Python 不怕,C++ 要先除再乘:`a // gcd(a, b) * b`。

### 應用場景

- Fraction 的分子分母約分
- Find Greatest Common Divisor of Array (1979)
- X of a Kind in a Deck of Cards (914)

### 擴展歐幾里得

`ax + by = gcd(a, b)` 求 x, y:

```python
def extgcd(a, b):
    if b == 0: return a, 1, 0
    g, x1, y1 = extgcd(b, a % b)
    return g, y1, x1 - (a // b) * y1
```

面試幾乎不考,但模反元素、線性同餘方程會用。

---

## 質數篩(Sieve of Eratosthenes)

> 找 ≤ n 的所有質數,O(n log log n)。

```python
def sieve(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(n**0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, n + 1, i):    # 從 i*i 開始即可
                is_prime[j] = False
    return [i for i in range(n + 1) if is_prime[i]]
```

**關鍵優化**:從 `i*i` 開始(更小的 composite 已經被更小的質因數篩掉了)。

### 線性篩(Euler Sieve,O(n))

```python
def linear_sieve(n):
    is_prime = [True] * (n + 1)
    primes = []
    is_prime[0] = is_prime[1] = False
    for i in range(2, n + 1):
        if is_prime[i]:
            primes.append(i)
        for p in primes:
            if i * p > n: break
            is_prime[i * p] = False
            if i % p == 0: break    # 關鍵剪枝
    return primes
```

`if i % p == 0: break` 保證每個合數只被「最小質因數」篩一次。面試極罕見,知道存在即可。

### 應用

- Count Primes (204)
- 最大公因數批量查詢

---

## 快速冪

```python
def fast_pow(base, exp, mod=None):
    result = 1
    base %= mod if mod else base
    while exp > 0:
        if exp & 1:
            result = result * base
            if mod: result %= mod
        base = base * base
        if mod: base %= mod
        exp >>= 1
    return result
```

**複雜度 O(log exp)**。Python 有 `pow(base, exp, mod)` 內建,且用的就是快速冪:

```python
pow(2, 100, 10**9 + 7)   # 直接用
```

面試可以直接 `pow`,但被問「你怎麼實作?」要能寫上面的迴圈。

---

## 模運算

**加**:`(a + b) % p`
**減**:`(a - b + p) % p`(避免 Python 之外的語言出負)
**乘**:`(a * b) % p`
**除**:`a * pow(b, p - 2, p) % p`(p 是質數時,用費馬小定理 Mod Inverse)

### 除法靠 Mod Inverse

模 p 意義下的 `1 / b` 叫模反元素。p 為質數時,`b^(p-2) mod p` 就是答案。

面試場景:組合數 `C(n, k) mod p`,分母要用 mod inverse。

---

## 組合數

### 直接計算(小 n)

```python
import math
math.comb(n, k)   # 3.8+
```

### 遞推(楊輝三角)

```python
def binom_table(N):
    C = [[0] * (N + 1) for _ in range(N + 1)]
    for i in range(N + 1):
        C[i][0] = 1
        for j in range(1, i + 1):
            C[i][j] = C[i-1][j-1] + C[i-1][j]
    return C
```

**適用**:多次查詢,n ≤ 1000。

### Mod 質數下

```python
MOD = 10**9 + 7
MAX = 10**6
fact = [1] * (MAX + 1)
inv_fact = [1] * (MAX + 1)

for i in range(1, MAX + 1):
    fact[i] = fact[i-1] * i % MOD
inv_fact[MAX] = pow(fact[MAX], MOD - 2, MOD)
for i in range(MAX - 1, -1, -1):
    inv_fact[i] = inv_fact[i+1] * (i+1) % MOD

def comb(n, k):
    if k < 0 or k > n: return 0
    return fact[n] * inv_fact[k] % MOD * inv_fact[n-k] % MOD
```

預處理 O(n),查詢 O(1)。競程常用,面試偶爾見。

---

## Factorization(質因數分解)

### 單數分解

```python
def factorize(n):
    factors = {}
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors[d] = factors.get(d, 0) + 1
            n //= d
        d += 1
    if n > 1:
        factors[n] = factors.get(n, 0) + 1
    return factors
```

**O(sqrt(n))**。若 n 很大(10^18 級)要 Pollard Rho,面試不會考。

---

## 面試常見數學題

### Count Primes (204)

直接 sieve。

### Happy Number (202)

數位平方和迭代,會收斂到 1 或進入 4 的環。用 set 偵測環,或快慢指針(Floyd)。

### Pow(x, n) (50)

快速冪。負指數特判。

```python
def my_pow(x, n):
    if n < 0: return 1 / my_pow(x, -n)
    if n == 0: return 1
    half = my_pow(x, n // 2)
    return half * half * (x if n & 1 else 1)
```

### Integer Break (343)

> 把 n 拆成若干正整數之和,最大化乘積。

**數學觀察**:盡量拆成 3,剩餘 2 或 4。

```python
def integer_break(n):
    if n == 2: return 1
    if n == 3: return 2
    q, r = divmod(n, 3)
    if r == 0: return 3 ** q
    if r == 1: return 3 ** (q - 1) * 4
    return 3 ** q * 2
```

**說明**:`e ≈ 2.718`,最佳的切塊大小是 3(離 e 最近的整數)。面試考的話能推導出「為什麼是 3」會加分。

### 丟番圖(Diophantine)類題

`ax + by = c` 有解的條件:`gcd(a, b) | c`。

### Water and Jug Problem (365)

> 有 x 升和 y 升的水桶,能否量出 z 升?

**答案**:z ≤ x+y 且 `gcd(x, y) | z`。Bezout's identity。

---

## 概率題(面試偶爾會問)

### Reservoir Sampling

> 從**未知長度的 stream** 隨機抽 k 個。

```python
import random

def reservoir_sampling(stream, k):
    reservoir = []
    for i, x in enumerate(stream):
        if i < k:
            reservoir.append(x)
        else:
            j = random.randint(0, i)
            if j < k:
                reservoir[j] = x
    return reservoir
```

**證明**:第 i 個元素留下來的機率 = k/i × (1 - k/(i+1)) × ... = k/n。

面試經典題:Random Pick with Weight (528)、Linked List Random Node (382)。

---

## 自我檢核

- [ ] `gcd(a, b)` 的輾轉相除為什麼 O(log)?
- [ ] 質數篩從 `i*i` 開始的理由?
- [ ] 快速冪 `pow(base, exp, mod)` 的時間複雜度?
- [ ] 模運算下的除法怎麼做?
- [ ] Integer Break 為什麼要盡量拆成 3?
- [ ] Reservoir sampling:掃到第 i 個時以什麼機率替換?

→ [Ch 26 白板流程](./26-whiteboard.md)
