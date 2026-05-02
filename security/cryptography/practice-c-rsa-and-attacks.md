# 練習 C — 手刻 RSA-2048 + 跑經典攻擊

> 目標：把 Part 5 的 RSA 學完整：手刻 RSA-2048 keygen / encrypt / decrypt（C + Python）、CRT 加速、PKCS#1 v1.5 padding，最後對自己的 weak key 跑三個經典攻擊（Wiener / Hastad broadcast / 簡化版 Bleichenbacher）。

## 任務規格

| Part | 內容 | 語言 |
|---|---|---|
| 1 | RSA-2048 keygen（Miller-Rabin + 隨機 prime） | Python |
| 2 | RSA encrypt/decrypt + CRT 加速 | Python |
| 3 | PKCS#1 v1.5 padding implement | Python |
| 4 | Wiener attack：產 weak d，破 | Python |
| 5 | Hastad broadcast attack：e=3 + 同訊息給 3 人 | Python |
| 6 | 簡化版 Bleichenbacher：local oracle，破特定 m | Python |
| 7 | C 版 RSA encrypt（用 GMP `mpz_powm`） | C |

## 期望輸出

```bash
$ python rsa.py keygen
[*] Generated 2048-bit RSA key
$ python rsa.py encrypt "hello"
$ python rsa.py decrypt
hello

$ python wiener.py
[*] target n bits: 1024
[*] expected d:  ...
[*] recovered d: ...
[+] Wiener attack succeeded

$ python hastad.py
[*] Sending m to 3 recipients with e=3
[+] Hastad attack: m recovered = "secret message"

$ python bleichenbacher.py
[*] Querying oracle 12345 times...
[+] Bleichenbacher: m recovered = "secret message"
```

## 實作步驟建議

### Step 1-2：RSA keygen + encrypt/decrypt + CRT

```python
import secrets
from sympy import isprime

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
    d = pow(e, -1, phi)
    return {'n': n, 'e': e, 'd': d, 'p': p, 'q': q}

def rsa_encrypt(m_int, key):
    return pow(m_int, key['e'], key['n'])

def rsa_decrypt_crt(c_int, key):
    p, q, d = key['p'], key['q'], key['d']
    dp = d % (p - 1)
    dq = d % (q - 1)
    qinv = pow(q, -1, p)
    m_p = pow(c_int, dp, p)
    m_q = pow(c_int, dq, q)
    h = (qinv * (m_p - m_q)) % p
    return m_q + h * q
```

### Step 3：PKCS#1 v1.5 padding

```python
def pkcs1_v15_pad(message: bytes, key_bytes: int) -> bytes:
    """加 padding 使長度 = key_bytes"""
    if len(message) > key_bytes - 11:
        raise ValueError("message too long")
    ps_len = key_bytes - len(message) - 3
    ps = bytearray()
    while len(ps) < ps_len:
        b = secrets.randbits(8)
        if b != 0:
            ps.append(b)
    return b'\x00\x02' + bytes(ps) + b'\x00' + message

def pkcs1_v15_unpad(padded: bytes) -> bytes:
    if padded[0] != 0x00 or padded[1] != 0x02:
        raise ValueError("invalid padding")
    sep = padded.find(b'\x00', 2)
    if sep < 10:
        raise ValueError("invalid padding")
    return padded[sep+1:]
```

### Step 4：Wiener attack

產一把 weak d 的 key（d < n^0.25 / 3），跑 Wiener：

```python
def gen_weak_rsa():
    """故意產 d 太小的 key"""
    bits = 1024
    while True:
        p = gen_prime(bits // 2)
        q = gen_prime(bits // 2)
        n = p * q
        phi = (p - 1) * (q - 1)
        # 直接選小 d
        bits_d = 200  # 比 1024/4=256 還小
        d = secrets.randbits(bits_d) | 1
        try:
            e = pow(d, -1, phi)
            if e < n:
                return {'n': n, 'e': e, 'd': d, 'p': p, 'q': q}
        except ValueError:
            continue

def continued_fraction(num, den):
    cf = []
    while den:
        cf.append(num // den)
        num, den = den, num % den
    return cf

def convergents(cf):
    h2, h1 = 1, cf[0]
    k2, k1 = 0, 1
    yield (h1, k1)
    for ai in cf[1:]:
        h, k = ai*h1 + h2, ai*k1 + k2
        yield (h, k)
        h2, h1 = h1, h
        k2, k1 = k1, k

def wiener(n, e):
    cf = continued_fraction(e, n)
    for k, d in convergents(cf):
        if k == 0: continue
        phi = (e * d - 1) // k
        if phi <= 0: continue
        s = n - phi + 1
        disc = s*s - 4*n
        if disc < 0: continue
        from sympy import isqrt
        sq = isqrt(disc)
        if sq*sq == disc:
            return d
    return None

# 測試
key = gen_weak_rsa()
recovered_d = wiener(key['n'], key['e'])
assert recovered_d == key['d']
print(f"[+] Wiener attack: d = {recovered_d}")
```

### Step 5：Hastad broadcast attack

```python
def hastad_attack(messages_with_keys, e=3):
    """
    messages_with_keys = [(c1, n1), (c2, n2), (c3, n3)]
    e = 3
    """
    cs = [c for c, _ in messages_with_keys]
    ns = [n for _, n in messages_with_keys]
    # CRT 合併
    from functools import reduce
    N = reduce(lambda a, b: a * b, ns)
    me = 0
    for c, n in messages_with_keys:
        Ni = N // n
        me += c * Ni * pow(Ni, -1, n)
    me %= N
    # 開 e 次方
    from sympy import integer_nthroot
    m, exact = integer_nthroot(me, e)
    if not exact:
        return None
    return m

# 測試
import secrets
m = int.from_bytes(b"hello", 'big')
keys = [rsa_keygen_e3() for _ in range(3)]  # e=3 版本
cts = [(pow(m, 3, k['n']), k['n']) for k in keys]
recovered = hastad_attack(cts)
assert recovered == m
```

### Step 6：簡化 Bleichenbacher

完整 Bleichenbacher 算法複雜（4 step、上百萬 query）。簡化版：對小 n 做演示：

```python
def bleichenbacher_simplified(c, e, n, oracle):
    """
    oracle(c) -> True 若 c 解密後 PKCS conformant
    回傳 m
    """
    # B = 2^(8 × (k-2)), k = key bytes
    k = (n.bit_length() + 7) // 8
    B = 2 ** (8 * (k - 2))
    
    # Step 1: blinding（這裡 c 已 conformant 跳過）
    s = 1
    M = [(2*B, 3*B - 1)]
    
    # 簡化版邏輯（完整版見 Bleichenbacher 1998 paper）
    # ...
```

實際做：用 cryptohack 上的 Bleichenbacher 範例題（CTF 風格）熟悉。完整實作放 reference solution。

### Step 7：C 版 RSA（用 GMP）

```c
#include <gmp.h>
#include <stdio.h>

void rsa_encrypt(mpz_t out, const mpz_t m, const mpz_t e, const mpz_t n) {
    mpz_powm(out, m, e, n);
}

void rsa_decrypt_crt(mpz_t out, const mpz_t c, const mpz_t p, const mpz_t q,
                     const mpz_t d) {
    mpz_t p_minus_1, q_minus_1, dp, dq, qinv, m_p, m_q, h, tmp;
    mpz_inits(p_minus_1, q_minus_1, dp, dq, qinv, m_p, m_q, h, tmp, NULL);
    
    mpz_sub_ui(p_minus_1, p, 1);
    mpz_sub_ui(q_minus_1, q, 1);
    mpz_mod(dp, d, p_minus_1);
    mpz_mod(dq, d, q_minus_1);
    mpz_invert(qinv, q, p);
    
    mpz_powm(m_p, c, dp, p);
    mpz_powm(m_q, c, dq, q);
    mpz_sub(h, m_p, m_q);
    mpz_mul(h, h, qinv);
    mpz_mod(h, h, p);
    mpz_mul(out, h, q);
    mpz_add(out, out, m_q);
    
    mpz_clears(p_minus_1, q_minus_1, dp, dq, qinv, m_p, m_q, h, tmp, NULL);
}

int main(void) {
    mpz_t n, e, d, p, q, m, c, decrypted;
    mpz_inits(n, e, d, p, q, m, c, decrypted, NULL);
    
    /* set values from keygen output */
    mpz_set_str(n, "...", 16);
    mpz_set_ui(e, 65537);
    /* ... */
    
    mpz_set_ui(m, 12345);
    rsa_encrypt(c, m, e, n);
    rsa_decrypt_crt(decrypted, c, p, q, d);
    
    gmp_printf("decrypted = %Zd\n", decrypted);
    
    mpz_clears(n, e, d, p, q, m, c, decrypted, NULL);
    return 0;
}
```

編譯：

```bash
gcc -O2 rsa.c -lgmp -o rsa_test
```

## 完整參考解答

**寫過再看**。

<details>
<summary>Wiener attack 完整版</summary>

```python
from sympy import isqrt

def wiener_attack(n, e):
    cf = continued_fraction(e, n)
    for h, k in convergents(cf):
        if k == 0:
            continue
        phi_candidate = (e * h - 1) // k
        if phi_candidate <= 0:
            continue
        s = n - phi_candidate + 1
        disc = s * s - 4 * n
        if disc < 0:
            continue
        sq = isqrt(disc)
        if sq * sq == disc:
            return h
    return None
```

</details>

<details>
<summary>Hastad attack 完整版</summary>

```python
def hastad_attack(triples, e):
    """triples = [(c, n), ...] 共 e 個"""
    from functools import reduce
    from sympy import integer_nthroot
    N = reduce(lambda a, b: a * b, [n for _, n in triples])
    M_e = 0
    for c, n in triples:
        N_i = N // n
        N_i_inv = pow(N_i, -1, n)
        M_e = (M_e + c * N_i * N_i_inv) % N
    m, exact = integer_nthroot(M_e, e)
    if not exact:
        return None
    return m
```

</details>

## 測試用例

1. **RSA 一致性**：keygen → encrypt → decrypt 還原原文 1000 次
2. **CRT vs 普通**：兩種 decrypt 結果一致，CRT 約 4× 快
3. **Wiener 對 d < n^(1/4)/3 的 key 100% 成功**
4. **Hastad e=3, 3 個 recipient 必成功**
5. **修補後**：用 OAEP 取代 PKCS#1 v1.5 → Bleichenbacher 失敗

## 自我檢核

- [ ] 我能寫 RSA-2048 keygen + CRT decrypt（Python）
- [ ] 我能用 Wiener 破 weak d（< n^(1/4)/3）的 RSA
- [ ] 我能用 Hastad 對 e=3 + 3 同訊息攻擊
- [ ] 我用 GMP 寫了 C 版 RSA encrypt/decrypt
- [ ] 我能解釋為什麼 OAEP 修好了 Bleichenbacher
- [ ] 我能說出至少 5 條 RSA 安全用法的紀律

下一個 Part 進 AEAD（authenticated encryption）— 把 Part 3 的對稱加密與 Part 4 的 MAC 整合。

→ [Ch 25 AEAD 概念](./25-aead-concepts.md)
