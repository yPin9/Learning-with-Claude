# Ch 31 — ML-KEM (Kyber)：Module-LWE 上的 Key Encapsulation

> **目標**：能追蹤 ML-KEM（前身 CRYSTALS-Kyber）的 KeyGen / Encapsulate / Decapsulate 全流程，理解 NTT 如何加速多項式乘法、compression 為什麼導致 decryption failure 不為零，掌握 FIPS 203 的三組參數（Kyber-512 / 768 / 1024）。

## 為什麼需要這個？

Ch 29 告訴你 Shor's algorithm 打爆了所有基於 factoring / DLP 的公鑰密碼。Ch 30 告訴你 NIST 選了 lattice 問題作為替代。

ML-KEM（FIPS 203）是 NIST 的 **PQC 主力 KEM**。Chrome、Cloudflare、Signal 已經在部署它。如果你在做任何需要「安全交換密鑰」的事情（TLS、VPN、messaging），你未來幾年一定會碰到 ML-KEM。

這一章是深挖章。讀完之後，你會理解：
- KEM 和 PKE 的區別
- Kyber 的三個核心操作（KeyGen、Encaps、Decaps）裡每一步在做什麼
- NTT 為什麼能把多項式乘法從 O(n²) 降到 O(n log n)
- Compression 帶來的 decryption failure 是怎麼回事

## 先建立直覺

想像你要和對方交換一個共享秘密（shared secret），但通訊通道被竊聽：

```
傳統做法（DH / ECDH）：
  Alice → Bob: g^a mod p
  Bob → Alice: g^b mod p
  shared secret = g^(ab) mod p
  ← Shor 打爆這個

PQC 做法（KEM）：
  Alice 有公鑰 pk、私鑰 sk
  Bob 用 pk 封裝（encapsulate）→ 得到 (ciphertext, shared_secret)
  Bob → Alice: ciphertext
  Alice 用 sk 解封裝（decapsulate） → 得到同一個 shared_secret
  ← 基於 Module-LWE，量子安全
```

KEM（Key Encapsulation Mechanism，密鑰封裝機制）和 PKE（Public-Key Encryption，公鑰加密）的區別：

```
PKE：加密任意明文 m
  Encrypt(pk, m) → ciphertext
  Decrypt(sk, ciphertext) → m

KEM：只封裝一個隨機的 shared secret
  Encapsulate(pk) → (ciphertext, shared_secret)
  Decapsulate(sk, ciphertext) → shared_secret

為什麼 KEM 夠用？
  因為實務上我們用 shared_secret 作為對稱密鑰（AES-256）
  然後用 AES-256 加密任意長度的資料
  → KEM + 對稱加密 = 完整的加密方案（hybrid encryption）
  → TLS 1.3 就是這麼做的
```

## 核心概念：ML-KEM 的三個操作

### 參數概覽

```
ML-KEM 的參數（FIPS 203）：

  n = 256           多項式的次數（degree）
  q = 3329          模數
  Rq = Zq[X]/(X^256 + 1)   多項式環

  ┌───────────┬─────┬──────────┬──────────┬────────────┬────────────┐
  │ 變體       │  k  │ pk (B)   │ ct (B)   │ 安全等級    │ NIST level │
  ├───────────┼─────┼──────────┼──────────┼────────────┼────────────┤
  │ Kyber-512 │  2  │   800    │   768    │ ~128-bit   │ 1          │
  │ Kyber-768 │  3  │  1184    │  1088    │ ~192-bit   │ 3          │
  │ Kyber-1024│  4  │  1568    │  1568    │ ~256-bit   │ 5          │
  └───────────┴─────┴──────────┴──────────┴────────────┴────────────┘

  k 越大 → 安全性越高 → key / ciphertext 越大 → 速度越慢
```

### KeyGen：生成密鑰對

```
KeyGen() → (pk, sk)

  1. 用 seed ρ 確定性地生成公開矩陣 A ∈ Rq^{k×k}
     （A 由 ρ 展開，所以 pk 只需要存 ρ + t，不用存整個 A）

  2. 隨機生成秘密向量 s ∈ Rq^k
     （每個多項式的係數從 CBD(η₁) 取樣——小整數）

  3. 隨機生成誤差向量 e ∈ Rq^k
     （也是從 CBD(η₁) 取樣的小多項式）

  4. 計算 t = As + e  (在 Rq^k 上)

  5. 公鑰 pk = (ρ, t)     ← 公開
     私鑰 sk = s           ← 保密

  直覺：
    t ≈ As（因為 e 很小）
    知道 A 和 t，但因為 e 的存在，無法恢復 s
    這就是 Module-LWE 問題
```

```
CBD（Centered Binomial Distribution）：
  取 2η 個隨機 bits，前 η 個加起來 - 後 η 個加起來
  結果在 [-η, η] 之間
  
  η=2 的範例：
    bits = 1,0,1,1 → (1+0) - (1+1) = -1
    bits = 1,1,0,0 → (1+1) - (0+0) = 2

  為什麼用 CBD 而不是 Gaussian？
  → CBD 取樣更快（只需要 bit 運算）
  → 安全性分析顯示 CBD 夠用
```

### 範例一：簡化版 KeyGen

```python
"""
簡化版 ML-KEM KeyGen（教學用，不是 production code）
用小參數示範概念
"""
import numpy as np
from hashlib import sha3_256, sha3_512, shake_128, shake_256

class SimplifiedKyber:
    """簡化版 Kyber，用小參數方便理解"""
    
    def __init__(self, n=16, k=2, q=97, eta1=2, eta2=2):
        """
        真正的 Kyber: n=256, k=2/3/4, q=3329
        這裡用小參數教學
        """
        self.n = n
        self.k = k
        self.q = q
        self.eta1 = eta1
        self.eta2 = eta2
        self.rng = np.random.default_rng(42)
    
    def _cbd(self, eta: int, shape: tuple) -> np.ndarray:
        """Centered Binomial Distribution 取樣"""
        bits_a = self.rng.integers(0, 2, size=shape + (eta,))
        bits_b = self.rng.integers(0, 2, size=shape + (eta,))
        return (bits_a.sum(axis=-1) - bits_b.sum(axis=-1)) % self.q
    
    def _poly_mul(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """多項式乘法 in Rq = Zq[X]/(X^n + 1)"""
        n, q = self.n, self.q
        result = np.zeros(2 * n - 1, dtype=np.int64)
        for i in range(n):
            for j in range(n):
                result[i + j] = (result[i + j] + int(a[i]) * int(b[j])) % q
        # mod X^n + 1
        reduced = np.zeros(n, dtype=np.int64)
        for i in range(2 * n - 1):
            idx = i % n
            sign = 1 if (i // n) % 2 == 0 else -1
            reduced[idx] = (reduced[idx] + sign * result[i]) % q
        return reduced
    
    def _matrix_vec_mul(self, A, s):
        """矩陣-向量乘法（每個元素是多項式）"""
        k, n, q = self.k, self.n, self.q
        result = [np.zeros(n, dtype=np.int64) for _ in range(k)]
        for i in range(k):
            for j in range(k):
                prod = self._poly_mul(A[i][j], s[j])
                result[i] = (result[i] + prod) % q
        return result
    
    def keygen(self):
        """KeyGen：生成公私鑰對"""
        k, n, q = self.k, self.n, self.q
        
        # 1. 生成隨機矩陣 A ∈ Rq^{k×k}
        A = [[self.rng.integers(0, q, size=n) for _ in range(k)] 
             for _ in range(k)]
        
        # 2. 秘密向量 s ∈ Rq^k（CBD 取樣，小係數）
        s = [self._cbd(self.eta1, (n,)) for _ in range(k)]
        
        # 3. 誤差向量 e ∈ Rq^k（CBD 取樣）
        e = [self._cbd(self.eta1, (n,)) for _ in range(k)]
        
        # 4. t = As + e
        As = self._matrix_vec_mul(A, s)
        t = [(As[i] + e[i]) % q for i in range(k)]
        
        pk = (A, t)
        sk = s
        return pk, sk
    
    def encrypt(self, pk, msg_bit: int):
        """
        CPA 加密（Kyber 的內部 PKE）
        msg_bit: 0 或 1（簡化版只加密一個 bit）
        """
        A, t = pk
        k, n, q = self.k, self.n, self.q
        
        # 隨機向量 r, 誤差 e1, e2
        r = [self._cbd(self.eta1, (n,)) for _ in range(k)]
        e1 = [self._cbd(self.eta2, (n,)) for _ in range(k)]
        e2 = self._cbd(self.eta2, (n,))
        
        # u = A^T r + e1
        AT = [[A[j][i] for j in range(k)] for i in range(k)]
        ATr = self._matrix_vec_mul(AT, r)
        u = [(ATr[i] + e1[i]) % q for i in range(k)]
        
        # v = t^T r + e2 + encode(msg)
        # encode: 0 → 0, 1 → q/2 (≈ q//2)
        tr = np.zeros(n, dtype=np.int64)
        for i in range(k):
            tr = (tr + self._poly_mul(t[i], r[i])) % q
        
        msg_encoded = np.full(n, (q // 2) * msg_bit, dtype=np.int64)
        v = (tr + e2 + msg_encoded) % q
        
        return (u, v)
    
    def decrypt(self, sk, ciphertext):
        """CPA 解密"""
        u, v = ciphertext
        s = sk
        k, n, q = self.k, self.n, self.q
        
        # m' = v - s^T u
        su = np.zeros(n, dtype=np.int64)
        for i in range(k):
            su = (su + self._poly_mul(s[i], u[i])) % q
        
        noisy_msg = (v - su) % q
        
        # decode: 如果接近 q/2 → 1, 如果接近 0 → 0
        half_q = q // 2
        decoded_bits = []
        for coeff in noisy_msg:
            # 到 0 的距離 vs 到 q/2 的距離
            dist_to_0 = min(int(coeff), q - int(coeff))
            dist_to_half = abs(int(coeff) - half_q)
            decoded_bits.append(1 if dist_to_half < dist_to_0 else 0)
        
        return decoded_bits[0]  # 簡化版只看第一個係數

# 測試
kyber = SimplifiedKyber(n=16, k=2, q=97)
pk, sk = kyber.keygen()

# 加密 0
ct0 = kyber.encrypt(pk, 0)
pt0 = kyber.decrypt(sk, ct0)
print(f"加密 0 → 解密得到 {pt0}")

# 加密 1
ct1 = kyber.encrypt(pk, 1)
pt1 = kyber.decrypt(sk, ct1)
print(f"加密 1 → 解密得到 {pt1}")

# 驗證多次
success = 0
trials = 100
for _ in range(trials):
    for bit in [0, 1]:
        ct = kyber.encrypt(pk, bit)
        pt = kyber.decrypt(sk, ct)
        if pt == bit:
            success += 1

print(f"\n正確率: {success}/{trials*2} = {success/(trials*2)*100:.1f}%")
# 在小參數下可能不是 100%，這就是 decryption failure
```

## 底層機制：為什麼解密能成功？

### 數學推導

```
加密：
  u = A^T r + e₁
  v = t^T r + e₂ + encode(m)

解密：
  m' = v - s^T u
     = (t^T r + e₂ + encode(m)) - s^T(A^T r + e₁)
     = t^T r + e₂ + encode(m) - s^T A^T r - s^T e₁

  因為 t = As + e（KeyGen），所以 t^T = s^T A^T + e^T
  代入：
     = (s^T A^T + e^T) r + e₂ + encode(m) - s^T A^T r - s^T e₁
     = s^T A^T r + e^T r + e₂ + encode(m) - s^T A^T r - s^T e₁
     = encode(m) + (e^T r + e₂ - s^T e₁)
                    └──────────────────┘
                     噪音項（小，因為 s, e, r, e₁, e₂ 都是小值）

  所以 m' ≈ encode(m) + small_noise

  如果 |noise| < q/4：
    encode(0) = 0 → m' 接近 0 → decode 成 0  ✓
    encode(1) = q/2 → m' 接近 q/2 → decode 成 1  ✓

  如果 |noise| ≥ q/4：
    decode 可能出錯 → decryption failure!
```

```
視覺化（q = 3329）：

  0 ──────────── q/4 ──────────── q/2 ──────────── 3q/4 ──────────── q
  │    decode=0    │   decode=1    │    decode=1    │    decode=0    │
  │                │               │                │                │

  encode(0) = 0
    ↓ + noise
    ↓ 如果落在 [0, q/4) 或 (3q/4, q) → 正確 decode 為 0
    ↓ 如果落在 [q/4, 3q/4] → 錯誤！

  encode(1) = q/2 ≈ 1665
    ↓ + noise
    ↓ 如果落在 [q/4, 3q/4] → 正確 decode 為 1
    ↓ 如果落在 [0, q/4) 或 (3q/4, q) → 錯誤！
```

### NTT：加速多項式乘法

```
問題：
  Kyber 的核心運算是在 Rq = Zq[X]/(X^256 + 1) 上做多項式乘法
  樸素算法：O(n²) = O(256²) = 65,536 次乘法
  每次 KeyGen / Encaps / Decaps 要做很多次 → 太慢

解法：NTT（Number Theoretic Transform，數論變換）
  和 FFT（Fast Fourier Transform）原理相同
  FFT 在複數上做 → NTT 在有限域 Zq 上做

NTT 的直覺：
  ┌──────────┐     NTT      ┌──────────┐
  │ a(X)     │ ──────────→  │ â[0..n-1]│   coefficient → evaluation
  │ b(X)     │ ──────────→  │ b̂[0..n-1]│
  └──────────┘              └──────────┘
                                 │
                                 ↓  逐點相乘：ĉ[i] = â[i] · b̂[i]
                                 │  O(n) 次乘法
  ┌──────────┐    INTT      ┌──────────┐
  │ c(X)     │ ←──────────  │ ĉ[0..n-1]│   evaluation → coefficient
  │= a·b     │              │          │
  └──────────┘              └──────────┘

  NTT:  O(n log n)
  逐點: O(n)
  INTT: O(n log n)
  總計: O(n log n)  vs  樸素的 O(n²)

  n=256 時：
    樸素: 65,536 次乘法
    NTT:  256 × 8 = 2,048 次乘法 + overhead
    加速約 30 倍
```

為什麼 NTT 能在 Zq 上做？

```
FFT 需要 n 次單位根（ω^n = 1）
在複數上: ω = e^{2πi/n}（永遠存在）
在 Zq 上: 需要 q ≡ 1 (mod 2n)

Kyber 的 q = 3329
  3329 = 13 × 256 + 1  → 3329 ≡ 1 (mod 512) ✓
  所以 Z_3329 中存在 512 次單位根
  Kyber 的 NTT 可以正常運作

這不是巧合——q = 3329 是精心選的
```

### Compression：縮小 ciphertext

```
問題：
  u ∈ Rq^k 和 v ∈ Rq 的每個係數都是 [0, q-1] 的整數
  q = 3329 需要 12 bits 表示
  raw ciphertext = k × 256 × 12 + 256 × 12 bits = 很大

解法：compression（壓縮）
  Compress_d(x) = round(2^d / q × x) mod 2^d
  Decompress_d(x) = round(q / 2^d × x)

  把 [0, q-1] 壓到 [0, 2^d - 1]（更少的 bits）
  Kyber-512: u 壓成 d_u=10 bits, v 壓成 d_v=4 bits

代價：壓縮是有損的（lossy）
  Decompress(Compress(x)) ≈ x（不是精確等於 x）
  這個近似誤差加到解密的噪音裡 → 增加 decryption failure 機率

Kyber-512 的 decryption failure probability：
  Pr[failure] ≈ 2^{-139}
  在宇宙的壽命裡跑 Kyber，也幾乎不可能碰到一次 failure
```

## 進一步用法：從 CPA-PKE 到 CCA-KEM

### 範例二：Fujisaki-Okamoto Transform

```
Kyber 的內部 PKE 只有 CPA 安全（chosen-plaintext attack）
但 KEM 需要 CCA 安全（chosen-ciphertext attack）

Fujisaki-Okamoto (FO) transform 把 CPA-PKE 升級成 CCA-KEM：

Encapsulate(pk):
  1. 生成隨機 m（32 bytes）
  2. (K, r) = G(m ‖ H(pk))     ← G = SHA3-512, H = SHA3-256
  3. ct = Encrypt(pk, m; r)      ← 用 r 作為隨機性（確定性加密）
  4. shared_secret = KDF(K ‖ H(ct))
  5. return (ct, shared_secret)

Decapsulate(sk, ct):
  1. m' = Decrypt(sk, ct)
  2. (K', r') = G(m' ‖ H(pk))
  3. ct' = Encrypt(pk, m'; r')   ← 重新加密
  4. if ct' == ct:                ← 驗證：密文一致嗎？
       shared_secret = KDF(K' ‖ H(ct))
     else:
       shared_secret = KDF(z ‖ H(ct))  ← z 是 sk 的一部分，random fallback
  5. return shared_secret
```

```
FO 的關鍵巧妙之處：

  重新加密（re-encryption）檢查：
    - 解密得到 m' 後，用 m' 重新加密
    - 如果結果和收到的 ct 一致 → m' 是正確的
    - 如果不一致 → 有人篡改了 ct → 回傳 random 值

  為什麼這能防 CCA？
    CCA 攻擊者提交修改過的 ct' 想得到解密資訊
    → FO 檢測到 ct' 不是合法的加密結果 → 回傳 random
    → 攻擊者得不到任何有用的資訊

  implicit rejection：
    不回傳 "error"，而是回傳一個看起來合法的 random shared secret
    → 攻擊者無法區分 "成功" 和 "失敗"
    → 這對 side-channel 防護很重要
```

FO transform 的完整 Python 實作留在練習 D（practice-d-kyber512.md）的 Phase 4。

## 對比與取捨

| 特性 | ML-KEM (Kyber) | X25519 (ECDH) | RSA-2048 (KEM) | Classic McEliece |
|---|---|---|---|---|
| 安全假設 | Module-LWE | ECDLP | factoring | syndrome decoding |
| 量子安全 | ✓ | ✗ | ✗ | ✓ |
| 公鑰大小 | 800–1568 B | 32 B | 256 B | ~260 KB |
| 密文大小 | 768–1568 B | 32 B | 256 B | ~128 B |
| shared secret | 32 B | 32 B | ≤ 256 B | 32 B |
| KeyGen 速度 | ~10 μs | ~30 μs | ~ms | ~100 ms |
| Encaps 速度 | ~15 μs | ~30 μs | ~μs | ~μs |
| Decaps 速度 | ~15 μs | ~30 μs | ~ms | ~ms |
| NIST 標準 | FIPS 203 | — | — | Round 4 |
| Decryption failure | ~2⁻¹³⁹ | 0 | 0 | 0 |
| 部署現狀 | Chrome, CF, Signal | 到處都是 | 到處都是 | 實驗階段 |

| ML-KEM 參數 | k | pk (B) | ct (B) | NIST Level | 對標 |
|---|---|---|---|---|---|
| ML-KEM-512 | 2 | 800 | 768 | 1 | AES-128 |
| ML-KEM-768 | 3 | 1184 | 1088 | 3 | AES-192 |
| ML-KEM-1024 | 4 | 1568 | 1568 | 5 | AES-256 |

## 踩雷集錦

1. **「Kyber 的 public key 太大，不能用」**：Kyber-768 的 pk = 1184 bytes，ciphertext = 1088 bytes。和 RSA-2048 的公鑰（256 bytes）比是大了 4 倍，但和 Classic McEliece 的 ~260 KB 比是小了 220 倍。在 TLS handshake 中增加 ~2 KB 的開銷，對現代網路來說完全可以接受。

2. **「Decryption failure 代表不可靠」**：Kyber-512 的 failure probability 是 2⁻¹³⁹。你跑 2¹³⁹ 次 decapsulation 才會碰到一次 failure——這比宇宙中的原子數（~2²⁶⁵）還少。Decryption failure 在實務上不是問題，但在安全性證明中需要考慮（adversary 可能故意構造 failure case）。

3. **「用 Python 實作 Kyber 就行了」**：Python 實作只能用來學習。Production code 必須用 constant-time 實作（防止 timing side-channel），而且需要通過 NIST 的 Known Answer Tests。用 liboqs 或 pqcrypto。

4. **「NTT 只是加速——去掉它也能跑」**：沒有 NTT，Kyber 的多項式乘法是 O(n²) = O(65536)。有 NTT 是 O(n log n) ≈ O(2048)。去掉 NTT 後 Kyber 會慢 30 倍以上，在嵌入式裝置上可能不可行。NTT 不是可選的最佳化——它是設計的核心部分。

5. **「ML-KEM 和 Kyber 是不同的東西」**：ML-KEM（FIPS 203）是 Kyber 的標準化版本。NIST 在標準化過程中做了一些小修改（例如 hash function 的選擇），但核心數學完全相同。論文裡叫 Kyber，標準裡叫 ML-KEM。

## 進階：再往深一層

### NTT 的完整蝴蝶操作

Kyber 的 NTT 是 128-point NTT（不是 256-point），因為 X^256+1 在 Z_3329 上分解成 128 個二次因子。每層做 Cooley-Tukey butterfly：`a' = a + ω·b`, `b' = a - ω·b`（mod q）。7 層，每層 128 次蝴蝶 → 共 896 次乘法。實作中用 Montgomery multiplication 加速模乘法。

### Hybrid KEM：目前的部署方式

Chrome、Cloudflare 的 TLS 1.3 hybrid key exchange 用 `X25519 + ML-KEM-768`，shared secret = HKDF(X25519_ss || ML-KEM_ss)。Signal 的 PQXDH 也是 X25519 + ML-KEM-768。Hybrid 的好處：ML-KEM 被打破 → X25519 仍然保護；量子電腦到來 → ML-KEM 保護。必須兩邊都被打破才不安全。

注意：FIPS 203 和 Kyber Round 3 submission 有小差異（hash domain separation、key format），兩者不兼容。用 FIPS 203 的 KAT 驗證實作。

## 動手練習

1. **手算 NTT butterfly**：在 Z₁₇ 上（q=17），用 ω=2 對向量 [3, 5] 做一次蝴蝶操作。驗證 inverse 操作能還原。

2. **Compression 誤差**：用 q=3329, d=10，計算 Compress₁₀(1234) 和 Decompress₁₀(Compress₁₀(1234))。比較原始值和解壓後的值，計算誤差。

3. **Decryption failure 模擬**：修改範例一的程式碼，把 q 減小到 17（增大 failure probability），跑 10,000 次 encrypt/decrypt，統計 failure rate。

4. **Key size 計算**：用 ML-KEM-768 的參數（k=3, n=256, q=3329, d_t=12, d_u=10, d_v=4），手動計算公鑰和密文的 byte 數。（提示：公鑰 = k×n×d_t/8 + 32 bytes seed；密文 = k×n×d_u/8 + n×d_v/8）

## 本章重點整理

- ML-KEM 是 KEM（Key Encapsulation Mechanism），不是 PKE——它封裝一個 random shared secret，配合對稱加密使用
- 核心數學：Module-LWE，公鑰 t = As + e，加密/解密靠噪音項在 q/4 範圍內
- NTT 把多項式乘法從 O(n²) 加速到 O(n log n)，是 Kyber 設計的核心部分
- Compression 是有損的，導致 decryption failure probability 不為零（但 ~2⁻¹³⁹，實務上不是問題）
- Fujisaki-Okamoto transform 把 CPA 安全的 PKE 升級成 CCA 安全的 KEM
- Kyber-768 的 pk = 1184B、ct = 1088B，在 TLS 中增加 ~2 KB 是可接受的

## 自我檢核

- [ ] 能區分 KEM 和 PKE，並解釋為什麼 KEM 加上對稱加密就夠用
- [ ] 能追蹤 KeyGen 的流程：A 從 seed 生成、s 和 e 從 CBD 取樣、t = As + e
- [ ] 能解釋為什麼解密能成功（噪音項 e^T r + e₂ - s^T e₁ 夠小）
- [ ] 能解釋 NTT 的作用和為什麼 q = 3329 被選中
- [ ] 能解釋 compression 和 decryption failure 的關係
- [ ] 能描述 Fujisaki-Okamoto transform 的 re-encryption 檢查
- [ ] 能比較 ML-KEM-512 / 768 / 1024 的 key size 和安全等級

## 延伸閱讀

- **NIST FIPS 203, "Module-Lattice-Based Key-Encapsulation Mechanism Standard"（2024）**
  - **讀哪裡**：Section 4–7（algorithm specification）
  - **學什麼**：完整的 ML-KEM 規格——每個步驟的精確定義
  - **關聯**：本章簡化版的正式版本

- **Roberto Avanzi et al., "CRYSTALS-Kyber Algorithm Specifications"（Round 3 Submission, 2021）**
  - **讀哪裡**：Section 1（overview）和 Section 2（algorithm）
  - **學什麼**：Kyber 原始設計團隊的技術報告，比 FIPS 203 多了設計動機的說明
  - **關聯**：本章設計決策的原始來源

- **Thomas Pöppelmann & Tim Güneysu, "Towards Practical Lattice-Based Public-Key Encryption on Reconfigurable Hardware"（2013）**
  - **讀哪裡**：Section 3（NTT implementation）
  - **學什麼**：NTT 在硬體上的高效實作——蝴蝶操作的細節
  - **關聯**：本章 NTT 段落的延伸

- **Eiichiro Fujisaki & Tatsuaki Okamoto, "Secure Integration of Asymmetric and Symmetric Encryption Schemes"（1999）**
  - **讀哪裡**：Section 2–3（FO transform 的定義和安全性證明）
  - **學什麼**：CPA → CCA 升級的理論基礎
  - **關聯**：本章 FO transform 段落的理論來源

→ [Ch 32 ML-DSA (Dilithium)](./32-ml-dsa-dilithium.md)
