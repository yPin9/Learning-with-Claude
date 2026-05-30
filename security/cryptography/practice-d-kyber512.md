# 練習 D — 簡化版 Kyber-512 KEM 實作

> **目標**：用 Python 從零實作簡化版 Kyber-512 KEM（Key Encapsulation Mechanism）。走過 NTT / 多項式運算 / KeyGen / Encapsulate / Decapsulate 的完整流程，驗證 shared secret 一致性，測量 decryption failure probability。

## 前置條件

- 完成 Ch 30（Lattice 基礎、LWE、Module-LWE）
- 完成 Ch 31（ML-KEM 的完整流程、NTT、Compression）
- Python 3.11 + NumPy

## 規格總覽

```
本練習實作的簡化版 Kyber-512：

  和 FIPS 203 的主要簡化：
  1. 用樸素的 NTT（教學版），不用 Montgomery multiplication
  2. CBD 取樣用 NumPy random 模擬（不是 SHAKE-based PRF）
  3. 沒有 Fujisaki-Okamoto transform（只做 CPA-PKE，不做 CCA-KEM）
  4. 沒有 compression（Phase 5 加入 compression 並測量 failure rate）

  保留的核心數學：
  - Rq = Z_3329[X]/(X^256 + 1) 上的多項式運算
  - Module-LWE with k=2
  - NTT 加速多項式乘法
  - CBD 噪音分布
```

```
完整的 Kyber-512 參數（FIPS 203）：
  n = 256         多項式次數
  q = 3329        模數
  k = 2           模組維度
  η₁ = 3          秘密/誤差 CBD 參數
  η₂ = 2          加密誤差 CBD 參數
  d_u = 10        u 壓縮 bits
  d_v = 4         v 壓縮 bits
```

---

## Phase 1：NTT 和 Inverse NTT

NTT 是 Kyber 的效能核心。沒有 NTT，多項式乘法是 O(n²)；有 NTT 是 O(n log n)。

### 1.1 背景知識

```
Kyber 的 NTT 不是標準的 256-point NTT。
X^256 + 1 在 Z_3329 上可以分解成 128 個二次因子：
  X^256 + 1 = ∏_{i=0}^{127} (X² - ζ^{2i+1})  (mod 3329)

其中 ζ = 17 是 Z_3329 的 256 次原根（ζ^256 = 1 mod 3329）

所以 Kyber 的 NTT 是把一個 degree-255 多項式
映射成 128 個 degree-1 多項式（在 Z_3329[X]/(X² - ζ^{2i+1}) 上）

每個 NTT 係數實際上是一「對」值 (a, b)，表示 a + bX mod (X² - ζ^{2i+1})

這就是為什麼 NTT 後的逐點乘法不是單純的標量乘法，
而是在 Z_3329[X]/(X² - root) 上的二次多項式乘法。
```

### 1.2 實作任務

```python
"""
Phase 1: 實作 NTT 和 Inverse NTT

你的任務：填寫 TODO 區塊
"""
import numpy as np

# Kyber 參數
N = 256
Q = 3329
ZETA = 17  # 256th root of unity in Z_3329: 17^256 ≡ 1 (mod 3329)

def precompute_zetas():
    """
    預計算 NTT 需要的 twiddle factors (ζ 的冪次)
    
    Kyber 的 NTT 使用 bit-reversed order 的 ζ powers
    zetas[i] = ζ^{bitrev_7(i)} mod q
    
    Returns: list of 128 zeta values
    """
    zetas = [0] * 128
    # TODO: 計算 bit-reversed 的 ζ powers
    #
    # 步驟：
    # 1. 計算 ζ^0, ζ^1, ..., ζ^127 mod q
    # 2. 對 index 做 7-bit bit-reversal
    #    例如：1 (0000001) → 64 (1000000)
    #         2 (0000010) → 32 (0100000)
    # 3. zetas[i] = ζ^{bitrev(i)} mod q
    #
    # 提示：7-bit bit-reversal 因為 128 = 2^7
    
    return zetas

def ntt(f):
    """
    Cooley-Tukey butterfly NTT, 7 層
    Input: f = 256 coefficients in Z_q → Output: f_hat = NTT(f)
    
    每層的 butterfly: a' = a + ω·b, b' = a - ω·b (mod q)
    length 從 128 遞減到 2，zeta index k 從 1 遞增到 127
    
    TODO: 實作
    """
    f_hat = list(f)
    zetas = precompute_zetas()
    # TODO: 7-layer Cooley-Tukey butterfly
    return f_hat

def intt(f_hat):
    """
    Gentleman-Sande butterfly INTT（NTT 的逆）
    length 從 2 遞增到 128，zeta index k 從 127 遞減到 1
    最後乘以 N^{-1} mod Q = 3303
    
    TODO: 實作
    """
    f = list(f_hat)
    zetas = precompute_zetas()
    N_INV = pow(N, -1, Q)  # 3303
    # TODO: 7-layer Gentleman-Sande butterfly + multiply by N_INV
    return f

def poly_basemul(a_hat, b_hat):
    """
    NTT domain 的 base case 乘法：128 次二次多項式乘法
    
    每對 (a[2i], a[2i+1]) 表示 a0 + a1·X mod (X² - γ)
    (a0+a1X)(b0+b1X) mod (X²-γ) = (a0b0 + a1b1γ) + (a0b1 + a1b0)X
    
    64 組，每組 2 對（第二對用 -γ）
    
    TODO: 實作
    """
    c_hat = [0] * N
    zetas = precompute_zetas()
    # TODO: 64 iterations, each handling 2 pairs
    return c_hat

# ====== 驗證 ======

def poly_mul_naive(a, b):
    """樸素 O(n²) 多項式乘法（作為 NTT 的 reference）"""
    c = [0] * (2 * N - 1)
    for i in range(N):
        for j in range(N):
            c[i + j] = (c[i + j] + a[i] * b[j]) % Q
    # mod X^N + 1
    result = [0] * N
    for i in range(2 * N - 1):
        idx = i % N
        sign = 1 if (i // N) % 2 == 0 else -1
        result[idx] = (result[idx] + sign * c[i]) % Q
    return result

def test_ntt():
    """驗證：(1) NTT→INTT roundtrip (2) NTT乘法==樸素乘法 (3) 速度比較"""
    rng = np.random.default_rng(42)
    a = [int(x) for x in rng.integers(0, Q, size=N)]
    b = [int(x) for x in rng.integers(0, Q, size=N)]
    
    assert intt(ntt(a)) == a, "NTT roundtrip failed"
    
    c_naive = poly_mul_naive(a, b)
    c_ntt = intt(poly_basemul(ntt(a), ntt(b)))
    assert c_naive == c_ntt, "NTT mul != naive mul"
    
    # 速度比較（100 iterations）
    import time
    t0 = time.perf_counter()
    for _ in range(100): poly_mul_naive(a, b)
    t_naive = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(100): intt(poly_basemul(ntt(a), ntt(b)))
    t_ntt = time.perf_counter() - t0
    print(f"✓ NTT OK, speedup: {t_naive/t_ntt:.1f}x")
```

### 1.3 自我驗證

完成 Phase 1 後，你的程式應該通過以下檢查：

- [ ] `ntt(intt(f)) == f` 對任意 `f` 成立
- [ ] `intt(ntt(f)) == f` 對任意 `f` 成立
- [ ] NTT 乘法的結果和樸素乘法完全一致（不只是近似）
- [ ] NTT 乘法比樸素乘法快至少 5 倍（n=256 時通常 10-30 倍）

---

## Phase 2：Rq 上的多項式運算

建立完整的 `PolyRq` 類型，支援加法、減法、NTT 乘法、CBD 取樣。

### 2.1 實作任務

```python
"""
Phase 2: Rq = Z_3329[X]/(X^256 + 1) 的多項式運算

建立在 Phase 1 的 NTT 上
"""

class PolyRq:
    """Z_q[X]/(X^n + 1) 上的多項式"""
    
    def __init__(self, coeffs=None, n=256, q=3329):
        self.n = n
        self.q = q
        if coeffs is None:
            self.coeffs = [0] * n
        else:
            self.coeffs = [int(c) % q for c in coeffs[:n]]
            if len(self.coeffs) < n:
                self.coeffs += [0] * (n - len(self.coeffs))
    
    def __add__(self, other):
        """多項式加法 mod q"""
        return PolyRq(
            [(a + b) % self.q for a, b in zip(self.coeffs, other.coeffs)],
            self.n, self.q
        )
    
    def __sub__(self, other):
        """多項式減法 mod q"""
        return PolyRq(
            [(a - b) % self.q for a, b in zip(self.coeffs, other.coeffs)],
            self.n, self.q
        )
    
    def ntt_mul(self, other):
        """NTT → basemul → INTT, TODO"""
        pass
    
    @staticmethod
    def cbd(eta, rng, n=256, q=3329):
        """CBD(η): 取 2η bits, 前η和 - 後η和, 結果在[-η,η], TODO"""
        pass
    
    @staticmethod
    def random(rng, n=256, q=3329):
        return PolyRq([int(x) for x in rng.integers(0, q, size=n)], n, q)
    
    def compress(self, d):
        """Compress_d(x) = round(2^d / q * x) mod 2^d, TODO"""
        pass
    
    def decompress(self, d):
        """Decompress_d(x) = round(q / 2^d * x), TODO"""
        pass
    
    def to_msg(self):
        """每個 coeff → 1 bit: 接近 0 → 0, 接近 q/2 → 1, TODO"""
        pass
    
    @staticmethod
    def from_msg(msg_bytes, n=256, q=3329):
        """每個 bit → coeff: 0 → 0, 1 → round(q/2), TODO"""
        pass
    
    def inf_norm(self):
        """中心化無窮範數（coefficients centered around 0）"""
        return max(min(c, self.q - c) for c in self.coeffs)
    
    def __eq__(self, other):
        return self.coeffs == other.coeffs
    
    def __repr__(self):
        nonzero = [(i, c) for i, c in enumerate(self.coeffs) if c != 0]
        if not nonzero:
            return "0"
        terms = [f"{c}x^{i}" if i > 0 else str(c) for i, c in nonzero[:3]]
        s = " + ".join(terms)
        if len(nonzero) > 3:
            s += f" + ... ({len(nonzero)} terms)"
        return s


class PolyVec:
    """Rq^k 上的多項式向量"""
    
    def __init__(self, polys):
        self.polys = polys
        self.k = len(polys)
    
    def __add__(self, other):
        return PolyVec([a + b for a, b in zip(self.polys, other.polys)])
    
    def __sub__(self, other):
        return PolyVec([a - b for a, b in zip(self.polys, other.polys)])
    
    def inner_product(self, other):
        """∑ aᵢ·bᵢ (NTT mul + sum), TODO"""
        pass


class PolyMatrix:
    """Rq^{k×k} 多項式矩陣"""
    def __init__(self, rows):
        self.rows = rows
        self.k = len(rows)
    
    def mul_vec(self, vec):
        """A × v: result[i] = ∑_j A[i][j]·v[j], TODO"""
        pass
    
    def transpose(self):
        k = self.k
        return PolyMatrix([[self.rows[j][i] for j in range(k)] for i in range(k)])
    
    @staticmethod
    def random(k, rng, n=256, q=3329):
        return PolyMatrix([[PolyRq.random(rng, n, q) for _ in range(k)] for _ in range(k)])


# ====== 驗證 ======

def test_poly_ops():
    """驗證：add/sub, NTT mul, CBD, compress/decompress, msg encode/decode, mat-vec mul"""
    import os
    rng = np.random.default_rng(42)
    a, b = PolyRq.random(rng), PolyRq.random(rng)
    
    assert a + b - b == a, "Add/Sub failed"
    assert a.ntt_mul(b) == PolyRq(poly_mul_naive(a.coeffs, b.coeffs)), "NTT mul failed"
    assert max(PolyRq.cbd(3, rng).inf_norm() for _ in range(1000)) <= 3, "CBD range"
    
    a_rt = a.compress(10).decompress(10)
    max_err = max(min(abs(a.coeffs[i] - a_rt.coeffs[i]), Q - abs(a.coeffs[i] - a_rt.coeffs[i]))
                  for i in range(256))
    assert max_err < 5, f"Compress error too large: {max_err}"
    
    msg = os.urandom(32)
    assert PolyRq.from_msg(msg).to_msg() == msg, "Msg roundtrip failed"
    
    A = PolyMatrix.random(2, rng)
    s = PolyVec([PolyRq.random(rng), PolyRq.random(rng)])
    assert A.mul_vec(s).k == 2
    print("✓ All Phase 2 tests passed")
```

### 2.2 自我驗證

- [ ] `(a + b) - b == a` 對隨機 a, b 成立
- [ ] NTT 乘法和樸素乘法結果完全一致
- [ ] `CBD(η).inf_norm() ≤ η` 永遠成立
- [ ] `Decompress(Compress(x))` 的最大誤差 < q/(2^{d+1})
- [ ] `from_msg(m).to_msg() == m` 對任意 32 bytes 成立

---

## Phase 3：KeyGen, Encapsulate, Decapsulate

組合 Phase 1 和 Phase 2 的元件，實作完整的 Kyber-512 CPA-PKE。

### 3.1 實作任務

```python
"""
Phase 3: Kyber-512 CPA-PKE (不含 FO transform)

KeyGen, Encrypt, Decrypt
"""

class Kyber512:
    """
    簡化版 Kyber-512
    k=2, n=256, q=3329, eta1=3, eta2=2
    """
    
    def __init__(self, seed=None):
        self.n = 256
        self.q = 3329
        self.k = 2
        self.eta1 = 3      # secret / error CBD parameter
        self.eta2 = 2      # encryption error CBD parameter
        self.rng = np.random.default_rng(seed)
    
    def keygen(self):
        """A ← random, s,e ← CBD(η₁), t = As+e. pk=(A,t), sk=s. TODO"""
        pass
    
    def encrypt(self, pk, msg_bytes):
        """
        m = encode(msg), r←CBD(η₁), e₁←CBD(η₂), e₂←CBD(η₂)
        u = A^T r + e₁, v = t^T r + e₂ + m → ct = (u, v). TODO
        """
        pass
    
    def decrypt(self, sk, ciphertext):
        """m_noisy = v - s^T u, msg = decode(m_noisy). TODO"""
        pass


# ====== 驗證 ======

def test_kyber512_basic():
    """驗證：10 次隨機 msg 的 encrypt/decrypt 全部成功，錯誤 key 解密失敗"""
    import os
    kyber = Kyber512(seed=42)
    pk, sk = kyber.keygen()
    
    for i in range(10):
        msg = os.urandom(32)
        assert kyber.decrypt(sk, kyber.encrypt(pk, msg)) == msg, f"Trial {i} failed"
    print("✓ 10/10 correct")
    
    # 錯誤 key
    _, sk2 = Kyber512(seed=99).keygen()
    msg = os.urandom(32)
    assert kyber.decrypt(sk2, kyber.encrypt(pk, msg)) != msg
    print("✓ Wrong key fails")
```

### 3.2 驗證要點

完成 Phase 3 後檢查：

- [ ] KeyGen 生成的 t 的係數分布接近均勻（因為 As 主導）
- [ ] 10 次隨機 encrypt/decrypt 全部成功（在無 compression 的情況下）
- [ ] 用錯誤的 sk decrypt 會得到隨機垃圾
- [ ] 修改 ciphertext 的任何一個係數會導致 decryption 失敗

---

## Phase 4：驗證 Shared Secret 一致性

把 CPA-PKE 包裝成 KEM。

### 4.1 實作任務

```python
"""
Phase 4: 從 CPA-PKE 包裝成簡化版 KEM

不含完整的 Fujisaki-Okamoto transform
用 hash(msg) 作為 shared secret（簡化版）
"""
import hashlib

class Kyber512KEM:
    """簡化版 KEM（不含 FO transform）"""
    
    def __init__(self, seed=None):
        self.pke = Kyber512(seed=seed)
    
    def keygen(self):
        """KEM KeyGen"""
        return self.pke.keygen()
    
    def encapsulate(self, pk):
        """m←random(32B), ss=SHA3-256(m), ct=Encrypt(pk,m) → (ct,ss). TODO"""
        pass
    
    def decapsulate(self, sk, pk, ciphertext):
        """m'=Decrypt(sk,ct), ss=SHA3-256(m') → ss. TODO"""
        pass


# ====== 驗證 ======
# 跑 50 次 encapsulate/decapsulate，驗證 shared secret 全部一致
# 檢查 shared secret = 32 bytes，byte 分布接近均勻（mean ~127.5）
```

### 4.2 驗證要點

- [ ] 50 次 encapsulate/decapsulate 全部得到相同的 shared secret
- [ ] Shared secret 長度 = 32 bytes
- [ ] Shared secret 的 byte 分布接近均勻

---

## Phase 5：Compression 和 Decryption Failure Probability

加入 compression，觀察它對 correctness 的影響。

### 5.1 實作任務

```python
"""
Phase 5: 加入 Compression，測量 Decryption Failure Rate

Kyber-512 的 compression 參數：
  d_u = 10 (u vector 的每個係數壓到 10 bits)
  d_v = 4  (v 的每個係數壓到 4 bits)

壓縮公式：
  Compress_d(x) = round(2^d / q * x) mod 2^d
  Decompress_d(y) = round(q / 2^d * y)
"""

class Kyber512WithCompression(Kyber512):
    """帶 compression 的 Kyber-512"""
    
    def __init__(self, seed=None, d_u=10, d_v=4):
        super().__init__(seed)
        self.d_u = d_u
        self.d_v = d_v
    
    def encrypt(self, pk, msg_bytes):
        """
        和 Phase 3 相同，但在最後加入 compression
        
        TODO: 修改
        
        步驟 1-6: 和 Phase 3 相同
        步驟 7: u_compressed = Compress(u, d_u)
        步驟 8: v_compressed = Compress(v, d_v)
        步驟 9: ciphertext = (u_compressed, v_compressed)
        """
        pass
    
    def decrypt(self, sk, ciphertext):
        """
        在開始之前先 decompress
        
        TODO: 修改
        
        步驟 0a: u = Decompress(u_compressed, d_u)
        步驟 0b: v = Decompress(v_compressed, d_v)
        步驟 1-3: 和 Phase 3 相同
        """
        pass


def test_compression_failure():
    """
    測量不同壓縮參數下的 failure rate
    
    TODO: 對以下配置各跑 1000 次 encrypt/decrypt，統計 failure 數：
      - 無壓縮（預期 0 failure）
      - d_u=10, d_v=4（Kyber 標準，預期 0 failure in 1000 trials）
      - d_u=8, d_v=3（激進，預期 ~5-20 failures）
      - d_u=6, d_v=2（極端，預期 >100 failures）
    """
    pass

def test_noise_analysis():
    """
    分析 noise = e^T r + e2 - s^T e1 + compression_noise 的分布
    
    TODO: 修改 encrypt 讓它額外返回中間值，
    統計 noise 的 max 值，檢查是否 < q/4 = 832
    """
    pass
```

### 5.2 自我驗證

- [ ] 無壓縮版本 failure rate = 0
- [ ] 標準壓縮參數 (d_u=10, d_v=4) 在 1000 次試驗中 failure = 0
- [ ] d_u=6, d_v=2 的 failure rate > 10%
- [ ] 噪音分析顯示噪音的最大值遠小於 q/4（在標準參數下）

---

## 延伸挑戰（選做）

1. **加入 Fujisaki-Okamoto Transform**：re-encryption check + implicit rejection，使 KEM 成為 CCA-secure
2. **Constant-Time 分析**：找出所有 data-dependent branch，列出哪些需要改成 constant-time
3. **Known Answer Test**：從 NIST FIPS 203 KAT vectors 驗證你的實作和官方向量一致
4. **效能優化**：用 NumPy vectorized 取代 Python for loop，測量加速幅度
5. **和 liboqs 比較**：安裝 `liboqs-python`，比較你的 Python 和 C 實作的速度差距

---

## 參考資料

- **NIST FIPS 203, "Module-Lattice-Based Key-Encapsulation Mechanism Standard"（2024）** — ML-KEM 的完整規格
- **Avanzi et al., "CRYSTALS-Kyber Algorithm Specifications and Supporting Documentation"（Round 3）** — Kyber 的詳細技術報告
- **liboqs (Open Quantum Safe)** — ML-KEM 的 reference C 實作：https://github.com/open-quantum-safe/liboqs
- **kyber-py** — Python 的教學級 Kyber 實作：https://github.com/GiacomoPope/kyber-py
