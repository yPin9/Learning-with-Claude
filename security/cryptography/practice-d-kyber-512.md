# 練習 D — 簡化版 Kyber-512 KEM 實作（Python）

> 目標：把 Ch 30-31 的 lattice 與 Kyber 變成可跑的程式。實作簡化版 ML-KEM-512 KEM（先不做 NTT 加速版本）、用 SageMath 驗證 module-LWE 樣本、跑一輪 keygen → encap → decap 對照官方 KAT vectors。

## 任務規格

| Part | 內容 |
|---|---|
| 1 | 實作 Z_3329 polynomial 加減乘（暴力 O(n²) 不用 NTT） |
| 2 | CBD（centered binomial distribution）採樣 |
| 3 | K-PKE: KeyGen + Encrypt + Decrypt |
| 4 | ML-KEM: 加 FO transform 變 IND-CCA |
| 5 | 對照 NIST KAT (Known Answer Test) vector |
| 6 | （optional）NTT 加速 |

## 期望輸出

```bash
$ python ml_kem_512.py
[*] KeyGen
    pk: 800 bytes
    sk: 1632 bytes
[*] Encaps
    ct: 768 bytes
    K (Bob): a3f2...
[*] Decaps
    K (Alice): a3f2...
[+] Match!

$ python ml_kem_512.py kat
[+] KAT vector 1: pass
[+] KAT vector 2: pass
...
```

## 重要參考

- NIST FIPS 203（規範文件）：<https://csrc.nist.gov/pubs/fips/203/final>
- Reference C 實作：<https://github.com/pq-crystals/kyber>
- KAT files：FIPS 203 附帶或 PQClean repo

## 實作步驟建議

### Step 1：基礎 Z_3329 polynomial

```python
import secrets

q = 3329  # ML-KEM modulus
n = 256   # polynomial degree

def poly_add(a, b):
    return [(x + y) % q for x, y in zip(a, b)]

def poly_sub(a, b):
    return [(x - y) % q for x, y in zip(a, b)]

def poly_mul(a, b):
    """ R_q = Z_q[x] / (x^256 + 1) 的乘法 """
    out = [0] * (2 * n)
    for i in range(n):
        for j in range(n):
            out[i + j] = (out[i + j] + a[i] * b[j]) % q
    # mod x^256 + 1 → 高位減去
    result = [0] * n
    for i in range(n):
        result[i] = (out[i] - out[i + n]) % q
    return result
```

**O(n²)** — 對 n=256 慢但能跑。實際 production 用 NTT。

### Step 2：CBD 採樣

ML-KEM-512 用 η=3 的 CBD：

```python
def cbd(eta, seed_bytes):
    """從 seed 派生 CBD samples"""
    # Expand seed via SHAKE-256
    from Crypto.Hash import SHAKE256
    shake = SHAKE256.new()
    shake.update(seed_bytes)
    raw = shake.read(eta * n // 4)  # 每 sample 用 2*eta bits
    
    poly = []
    for i in range(n):
        a = sum((raw[i // (8 // eta)] >> j) & 1 for j in range(eta))  # crude
        b = sum((raw[i // (8 // eta)] >> (j + eta)) & 1 for j in range(eta))
        poly.append((a - b) % q)
    return poly
```

（實際取 bit 邏輯比這個複雜，照規範補完）

### Step 3：K-PKE

```python
def kpke_keygen():
    """K-PKE.KeyGen → (ek, dk)"""
    d = secrets.token_bytes(32)
    rho, sigma = expand(d)  # SHAKE
    
    # Matrix A from rho
    A = [[sample_uniform(rho, i, j) for j in range(k)] for i in range(k)]
    
    # s, e from sigma
    s = [cbd(eta_1, sigma + bytes([i])) for i in range(k)]
    e = [cbd(eta_1, sigma + bytes([k + i])) for i in range(k)]
    
    # t = A @ s + e
    t = [poly_add(sum_polys([poly_mul(A[i][j], s[j]) for j in range(k)]), e[i])
         for i in range(k)]
    
    ek = encode(rho, t)
    dk = encode(s)
    return ek, dk

def kpke_encrypt(ek, m, r):
    rho, t = decode(ek)
    A = matrix_from(rho)
    
    r_poly = [cbd(eta_1, r + bytes([i])) for i in range(k)]
    e1 = [cbd(eta_2, r + bytes([k + i])) for i in range(k)]
    e2 = cbd(eta_2, r + bytes([2*k]))
    
    u = [poly_add(sum_polys([poly_mul(A[j][i], r_poly[j]) for j in range(k)]), e1[i])
         for i in range(k)]   # A^T @ r + e1
    v = poly_add(poly_add(sum_polys([poly_mul(t[i], r_poly[i]) for i in range(k)]),
                          e2),
                 decompress(decode_msg(m)))
    
    return encode(compress_u(u), compress_v(v))

def kpke_decrypt(dk, ct):
    s = decode(dk)
    u, v = decompress_ct(ct)
    
    # m = v - s^T @ u
    diff = poly_sub(v, sum_polys([poly_mul(s[i], u[i]) for i in range(k)]))
    return encode_msg(compress_msg(diff))
```

`compress` / `decompress`、encoding 全是規範細節，要照 FIPS 203 補。

### Step 4：FO transform → ML-KEM

```python
def ml_kem_keygen():
    """ML-KEM.KeyGen → (ek, dk)"""
    z = secrets.token_bytes(32)        # implicit reject seed
    ek_pke, dk_pke = kpke_keygen()
    h = H(ek_pke)                       # H = SHA3-256
    dk = dk_pke + ek_pke + h + z        # full sk includes everything for FO
    return ek_pke, dk

def ml_kem_encaps(ek):
    m = secrets.token_bytes(32)
    K, r = G(m, H(ek))                   # G = SHA3-512
    ct = kpke_encrypt(ek, m, r)
    return K, ct

def ml_kem_decaps(dk, ct):
    dk_pke, ek_pke, h, z = parse_dk(dk)
    m_prime = kpke_decrypt(dk_pke, ct)
    K_prime, r_prime = G(m_prime, h)
    K_bar = KDF(z, ct)                   # implicit reject K
    
    ct_prime = kpke_encrypt(ek_pke, m_prime, r_prime)
    
    if ct == ct_prime:
        return K_prime
    else:
        return K_bar
```

**注意** const-time compare `ct == ct_prime` — production 必 const-time 防 side channel。教學版可暫不管。

### Step 5：KAT 驗證

NIST 在 FIPS 203 附 Known Answer Test vector。下載後：

```python
def run_kat(kat_file):
    with open(kat_file) as f:
        # parse format: 含 seed、ek、dk、ct、K
        # 用 deterministic mode (seed-based) 跑你的實作
        # 對照 expected output
        ...
```

KAT 的 seed-based mode 要在 `keygen` / `encaps` 接受 deterministic seed，而非每次 random。

## 完整參考解答

**自己寫一遍是這個練習的核心價值**。直接抄 reference 沒意義。

可參考 `kyber-py` Python 實作（< 1500 行）：<https://github.com/GiacomoPope/kyber-py>

學習用，**不要 production**（無 const-time 保證）。

## 測試用例

1. **Self-consistency**：keygen → encap → decap → K_alice == K_bob (1000 次)
2. **NIST KAT 至少 5 vector 通過**
3. **Tampered ct**：改 ct 一個 byte → decap 仍回某 K (implicit reject)，但與 K_bob 不同
4. **Performance**（學習版，不期望快）：keygen 應 < 100 ms

## 自我檢核

- [ ] 我能在 R_q = Z_3329[x]/(x^256+1) 上做加減乘
- [ ] 我能寫 CBD 採樣
- [ ] 我能寫 K-PKE keygen / encrypt / decrypt
- [ ] 我能用 FO transform 把 K-PKE 升到 ML-KEM
- [ ] 我能解釋 implicit reject 怎麼達到 IND-CCA
- [ ] 我能對照 NIST KAT vector 通過

下一個 Part 進 protocol 層 — TLS、Signal、Noise、protocol 失敗精選。

→ [Ch 34 TLS 1.3 握手](./34-tls-1-3.md)
