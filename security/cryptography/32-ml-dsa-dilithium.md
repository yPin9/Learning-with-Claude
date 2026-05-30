# Ch 32 — ML-DSA (Dilithium)：Fiat-Shamir + Module-LWE 的數位簽章

> **目標**：能解釋 ML-DSA（前身 CRYSTALS-Dilithium）的 KeyGen / Sign / Verify 完整流程，理解 Fiat-Shamir transform 如何把互動式證明變成非互動式簽章，理解 rejection sampling 的必要性，比較 ML-DSA 和 ECDSA 在 key / sig size 和效能上的差異。

## 為什麼需要這個？

Ch 31 解決了「量子安全的密鑰交換」（ML-KEM）。但密碼學不只需要加密——你還需要**數位簽章**來驗證身份和完整性。

ML-DSA（FIPS 204）是 NIST 的 **PQC 主力數位簽章方案**。每當你需要做以下事情，你都在用簽章：

- TLS 伺服器證明自己是真正的 google.com（certificate signature）
- 程式碼簽署（code signing）
- Document signing、JWT、SSH key

ML-DSA 和 ML-KEM 用的是同一套數學基礎（Module-LWE），但簽章和加密的運作邏輯截然不同。

## 先建立直覺

數位簽章要做的事：

```
Alice 有一對密鑰：(pk, sk)
Alice 用 sk 簽一份文件：sig = Sign(sk, message)
任何人用 pk 驗證：Verify(pk, message, sig) → true/false

安全需求：
  1. 拿到 pk 和多個 (message, sig) 對，無法偽造新的 sig
  2. 修改 message 的任何一個 bit，sig 就驗證失敗
```

ECDSA（Ch 23）用的是橢圓曲線離散對數——被 Shor 打爆。ML-DSA 用 Module-LWE 取代。

但要理解 ML-DSA，你需要先理解 **Fiat-Shamir transform**——它是從互動式零知識證明到非互動式簽章的橋樑。

```
互動式證明（Sigma Protocol）：
  Prover（Alice，有秘密 s）          Verifier（Bob）
    1. 選隨機 y，計算 w = commit(y)
       ────── w ──────────────────→
    2.                                 選隨機 challenge c
       ←──────── c ────────────────
    3. 計算 z = response(y, s, c)
       ────── z ──────────────────→
    4.                                 驗證 check(w, c, z, pk)

  三步：commit → challenge → response
  Bob 的 challenge 是隨機的 → 互動式
  
  問題：簽章需要非互動式（Bob 不在場）

Fiat-Shamir transform：
  把 Bob 的隨機 challenge 換成 hash：
    c = H(w ‖ message)
  
  → 不需要 Bob → 非互動式
  → c 看起來仍然是隨機的（Random Oracle Model）
  → commit + response 合在一起就是 signature
```

## 核心概念：ML-DSA 的三個操作

### 參數概覽

```
ML-DSA 的參數（FIPS 204）：

  n = 256, q = 8380417 = 2²³ - 2¹³ + 1
  Rq = Zq[X]/(X^256 + 1)

  ┌───────────┬────┬────┬──────────┬──────────┬────────────┬────────┐
  │ 變體       │ k  │ l  │ pk (B)   │ sig (B)  │ NIST Level │ 對標    │
  ├───────────┼────┼────┼──────────┼──────────┼────────────┼────────┤
  │ ML-DSA-44 │ 4  │ 4  │  1312    │  2420    │ 2          │ AES-128│
  │ ML-DSA-65 │ 6  │ 5  │  1952    │  3293    │ 3          │ AES-192│
  │ ML-DSA-87 │ 8  │ 7  │  2592    │  4595    │ 5          │ AES-256│
  └───────────┴────┴────┴──────────┴──────────┴────────────┴────────┘

  注意：k ≠ l（不像 Kyber 的 k×k 矩陣，Dilithium 用 k×l 矩陣）
  k = 公鑰向量維度
  l = 秘密向量維度
```

### KeyGen

```
KeyGen() → (pk, sk)

  1. 生成隨機種子 ρ，用 ρ 展開成公開矩陣 A ∈ Rq^{k×l}

  2. 隨機生成秘密向量 s₁ ∈ Rq^l（小係數，|coeff| ≤ η）
     隨機生成秘密向量 s₂ ∈ Rq^k（小係數，|coeff| ≤ η）

  3. 計算 t = As₁ + s₂  (mod q)

  4. 公鑰 pk = (ρ, t)
     私鑰 sk = (ρ, K, tr, s₁, s₂, t)
     （K = random seed for signing, tr = hash of pk）

  和 Kyber 的差異：
    Kyber:     t = As + e     → s₁ = s, s₂ = e
    Dilithium: t = As₁ + s₂   → 形式相同，但用途不同
    Kyber 用 t 來加密，Dilithium 用 t 來驗證簽章
```

### Sign（簽章）

```
Sign(sk, M) → sig

  這是最複雜的部分。核心流程：

  1. 計算 μ = H(tr ‖ M)              ← message digest

  2. 選隨機遮罩向量 y ∈ Rq^l
     （y 的係數在 [-γ₁+1, γ₁]，γ₁ 很大）

  3. 計算 commitment w = Ay (mod q)

  4. 把 w 的高位提取出來 → w₁
     （HighBits：只保留高位，去掉低位噪音）

  5. 計算 challenge c̃ = H(μ ‖ w₁)
     從 c̃ 擴展成多項式 c ∈ Rq
     （c 的係數只有 ±1 和 0，正好 τ 個 ±1）

  6. 計算 response z = y + c·s₁

  7. *** rejection sampling ***
     如果 z 的係數太大 → 重來（回到步驟 2）
     如果 w - c·s₂ 的低位太大 → 重來
     如果 hint 太多 → 重來

  8. 計算 hint h（幫助 verifier 重建 w₁）

  9. sig = (c̃, z, h)
```

為什麼需要 rejection sampling？

```
問題：
  z = y + c·s₁

  如果攻擊者看到很多 (c, z) 對：
    z₁ = y₁ + c₁·s₁
    z₂ = y₂ + c₂·s₁
    ...
  
  z 的分布會「洩漏」s₁ 的資訊
  （因為 z 的分布偏移了 c·s₁）

解法：rejection sampling
  只輸出那些 z 看起來「像是從 uniform distribution 來的」的簽章
  如果 z 的分布不夠像 uniform → 丟掉，重新選 y

  視覺化（一維簡化）：
  
  y 的分布：    ████████████████████     均勻在 [-γ₁, γ₁]
  z = y + cs₁:  ░░████████████████████░░  偏移了 cs₁
                     ↑ cs₁
  
  rejection 後：████████████████████     只保留在 [-γ₁+β, γ₁-β] 的部分
                 看起來又像均勻的了

  代價：平均要嘗試 ~4-7 次才能產出一個簽章
  ML-DSA-65：平均約 5.1 次嘗試
```

### 範例一：Fiat-Shamir 簽章（Schnorr-like 簡化版）

```python
"""
簡化版 Fiat-Shamir 簽章
展示 commit → challenge → response 的邏輯
不是 Dilithium 的精確實作，但展示核心 idea
"""
import hashlib
import numpy as np

class SimplifiedDilithiumSign:
    """
    極簡版 Module-LWE 簽章（教學用）
    """
    def __init__(self, n=8, k=2, l=2, q=97, eta=2, gamma1=30):
        self.n = n
        self.k = k
        self.l = l
        self.q = q
        self.eta = eta
        self.gamma1 = gamma1
        self.rng = np.random.default_rng(42)
    
    def _poly_mul(self, a, b):
        """Rq 上的多項式乘法 mod (X^n+1) — 和 Ch 31 相同"""
        n, q = self.n, self.q
        result = np.zeros(2 * n - 1, dtype=np.int64)
        for i in range(n):
            for j in range(n):
                result[i + j] = (result[i + j] + int(a[i]) * int(b[j])) % q
        reduced = np.zeros(n, dtype=np.int64)
        for i in range(2 * n - 1):
            idx = i % n
            sign = 1 if (i // n) % 2 == 0 else -1
            reduced[idx] = (reduced[idx] + sign * result[i]) % q
        return reduced
    
    def _mat_vec_mul(self, A, v, k_rows, l_cols):
        """矩陣向量乘法 — 和 Ch 31 相同"""
        result = [np.zeros(self.n, dtype=np.int64) for _ in range(k_rows)]
        for i in range(k_rows):
            for j in range(l_cols):
                result[i] = (result[i] + self._poly_mul(A[i][j], v[j])) % self.q
        return result
    
    def keygen(self):
        """KeyGen"""
        n, k, l, q, eta = self.n, self.k, self.l, self.q, self.eta
        
        A = [[self.rng.integers(0, q, size=n) for _ in range(l)]
             for _ in range(k)]
        
        s1 = [self.rng.integers(-eta, eta + 1, size=n) % q for _ in range(l)]
        s2 = [self.rng.integers(-eta, eta + 1, size=n) % q for _ in range(k)]
        
        As1 = self._mat_vec_mul(A, s1, k, l)
        t = [(As1[i] + s2[i]) % q for i in range(k)]
        
        pk = (A, t)
        sk = (A, s1, s2, t)
        return pk, sk
    
    def _hash_challenge(self, w1_bytes: bytes, msg: bytes) -> np.ndarray:
        """H(w1 || msg) → 小多項式 c（只有少數 ±1）"""
        h = hashlib.sha256(w1_bytes + msg).digest()
        c = np.zeros(self.n, dtype=np.int64)
        # 從 hash 取幾個位置設成 ±1
        for i in range(min(4, self.n)):
            pos = h[i * 2] % self.n
            sign = 1 if h[i * 2 + 1] % 2 == 0 else -1
            c[pos] = sign % self.q
        return c
    
    def _inf_norm(self, poly):
        """多項式的無窮範數（centered around 0）"""
        centered = np.array(poly, dtype=np.int64)
        centered = np.where(centered > self.q // 2, 
                           centered - self.q, centered)
        return int(np.max(np.abs(centered)))
    
    def sign(self, sk, message: bytes):
        """Sign with rejection sampling"""
        A, s1, s2, t = sk
        n, k, l, q, gamma1 = self.n, self.k, self.l, self.q, self.gamma1
        beta = self.eta * 4  # rejection bound
        
        attempts = 0
        while True:
            attempts += 1
            
            # 1. 隨機遮罩 y
            y = [self.rng.integers(-gamma1, gamma1 + 1, size=n) % q 
                 for _ in range(l)]
            
            # 2. commitment w = Ay
            w = self._mat_vec_mul(A, y, k, l)
            
            # 3. challenge c = H(w || msg)
            w_bytes = b''.join(w_i.tobytes() for w_i in w)
            c = self._hash_challenge(w_bytes, message)
            
            # 4. response z = y + c * s1
            z = []
            for j in range(l):
                cs1j = self._poly_mul(c, s1[j])
                zj = (y[j] + cs1j) % q
                z.append(zj)
            
            # 5. rejection sampling：z 的係數不能太大
            reject = False
            for j in range(l):
                if self._inf_norm(z[j]) >= gamma1 - beta:
                    reject = True
                    break
            
            if not reject:
                # 也檢查 w - cs2 的低位
                # （簡化版省略 hint 計算）
                break
            
            if attempts > 100:
                raise RuntimeError("Too many rejection attempts")
        
        return (c, z, attempts)
    
    def verify(self, pk, message: bytes, sig):
        """Verify"""
        A, t = pk
        c, z, _ = sig
        n, k, l, q, gamma1 = self.n, self.k, self.l, self.q, self.gamma1
        beta = self.eta * 4
        
        # 1. 檢查 z 的範數
        for j in range(l):
            if self._inf_norm(z[j]) >= gamma1 - beta:
                return False
        
        # 2. 計算 w' = Az - ct
        Az = self._mat_vec_mul(A, z, k, l)
        ct = []
        for i in range(k):
            cti = self._poly_mul(c, t[i])
            ct.append(cti)
        
        w_prime = [(Az[i] - ct[i]) % q for i in range(k)]
        
        # 3. 重新計算 challenge
        w_bytes = b''.join(w_i.tobytes() for w_i in w_prime)
        c_prime = self._hash_challenge(w_bytes, message)
        
        # 4. 比較 c 和 c'
        return np.array_equal(c, c_prime)

# 測試
dil = SimplifiedDilithiumSign(n=8, k=2, l=2, q=97, eta=2, gamma1=30)
pk, sk = dil.keygen()

msg = b"Hello, Post-Quantum World!"
sig = dil.sign(sk, msg)
c, z, attempts = sig
print(f"簽章成功（嘗試 {attempts} 次）")
print(f"challenge c: {c}")

valid = dil.verify(pk, msg, sig)
print(f"驗證結果: {valid}")

# 修改 message → 驗證失敗
valid_tampered = dil.verify(pk, b"Tampered message!", sig)
print(f"篡改後驗證: {valid_tampered}")
```

## 底層機制：驗證為什麼能成功？

### 數學推導

```
Sign 產出：
  w = Ay
  c = H(w ‖ M)
  z = y + c·s₁

Verify 計算：
  w' = Az - c·t
     = A(y + c·s₁) - c·(As₁ + s₂)
     = Ay + Ac·s₁ - cAs₁ - cs₂
     = Ay - cs₂
     = w - cs₂

  問題：w' = w - cs₂，不完全等於 w

  解法：HighBits
    Dilithium 只比較 w 和 w' 的「高位」(HighBits)
    如果 cs₂ 夠小（小於低位的容量） → HighBits(w) = HighBits(w')
    → c' = H(HighBits(w') ‖ M) = H(HighBits(w) ‖ M) = c  ✓

  但有時候 cs₂ 會影響高位（進位）
  → hint h 告訴 verifier 哪些位置需要調整
```

### 為什麼 rejection sampling 的次數是常數？

每次嘗試的成功機率 ≈ (γ₁ - β) / γ₁。ML-DSA-65：γ₁ = 2^19, β = 275 → 單次成功率 ~99.95%，但 LowBits check 把成功率拉低。實際平均嘗試次數：ML-DSA-44 ~4.25 次、ML-DSA-65 ~5.1 次、ML-DSA-87 ~3.85 次。嘗試次數有變異，但平均是常數。

## 進一步用法：和 ECDSA 的實務差異

### 範例二：Key Size 和 Signature Size 比較

```
方案               pk(B)    sig(B)   sign(μs)  verify(μs)  量子安全
──────────────────────────────────────────────────────────────────
ECDSA P-256        64       64       60        150         ✗
EdDSA (Ed25519)    32       64       50        70          ✗
ML-DSA-44          1312     2420     150       50          ✓
ML-DSA-65          1952     3293     250       80          ✓
ML-DSA-87          2592     4595     400       120         ✓
```

關鍵觀察：
1. ML-DSA-65 的公鑰是 Ed25519 的 61 倍，簽章是 51 倍
2. 但 verify 速度 ML-DSA-65 反而更快（無橢圓曲線標量乘法）
3. sign 較慢是因為 rejection sampling
4. TLS handshake：ECDSA 增加 ~128B，ML-DSA-65 增加 ~5.2 KB（現代寬頻下 <1ms RTT）

## 對比與取捨

| 特性 | ML-DSA-65 | ECDSA P-256 | EdDSA Ed25519 | SLH-DSA-128f |
|---|---|---|---|---|
| 安全假設 | Module-LWE | ECDLP | ECDLP | hash function |
| 量子安全 | ✓ | ✗ | ✗ | ✓ |
| 公鑰大小 | 1952 B | 64 B | 32 B | 32 B |
| 簽章大小 | 3293 B | 64 B | 64 B | 17,088 B |
| Sign 速度 | ~250 μs | ~60 μs | ~50 μs | ~ms |
| Verify 速度 | ~80 μs | ~150 μs | ~70 μs | ~ms |
| 確定性簽章 | ✓（hedged）| 需要 RFC 6979 | ✓ | ✓ |
| Stateful | 否 | 否 | 否 | 否 |
| NIST 標準 | FIPS 204 | FIPS 186-5 | FIPS 186-5 | FIPS 205 |

| 簽章方案選擇指南 | 推薦方案 | 理由 |
|---|---|---|
| 需要量子安全 + 小 sig | ML-DSA | 簽章最小的 PQC 標準 |
| 需要最保守的假設 | SLH-DSA | 只依賴 hash（Ch 33）|
| 暫時不需量子安全 | Ed25519 | 最小、最快 |
| 過渡期（hybrid）| Ed25519 + ML-DSA | 兩者都抵抗 |

## 踩雷集錦

1. **「PQC 簽章可以直接替換 ECDSA」**：ML-DSA-65 的公鑰 + 簽章合計 ~5.2 KB，是 Ed25519 的 ~54 倍。在 TLS certificate chain 中，一般有 2-3 個簽章。如果全部換成 ML-DSA，handshake 增加 ~10-15 KB。在桌面瀏覽器上不是問題，但在 IoT、衛星通訊、或者 UDP-based protocol（QUIC 的 initial packet 限制 1200 bytes）中可能需要特殊處理。

2. **「Rejection sampling 代表簽章時間不可預測」**：每次嘗試的成功機率接近常數（~20%），平均嘗試次數是 4-7 次。最壞情況（理論上）無上界，但實務上超過 20 次嘗試的機率遠低於 2⁻⁶⁴。如果你需要 constant-time 簽章（抵抗 timing side-channel），實作需要跑固定次數的迴圈並在最後選擇結果。

3. **「ML-DSA 的 verify 比 sign 快」**：這是和 ECDSA / RSA 的重大差異。ECDSA 的 verify 比 sign 慢（需要兩次點乘法 vs 一次）。ML-DSA 的 verify 比 sign 快 2-3 倍（verify 不需要 rejection sampling、不需要生成遮罩向量）。對於「簽一次、驗多次」的場景（code signing、certificate），這是優勢。

4. **「FALCON 比 Dilithium 好因為簽章更小」**：FALCON（NTRU lattice based）的簽章確實比 Dilithium 小（~660 bytes vs ~2420 bytes at Level 1），但 FALCON 的實作遠比 Dilithium 複雜（需要浮點數 Gaussian sampling，side-channel 防護困難）。NIST 把 Dilithium 選為主力標準、FALCON 留到後續標準化，就是因為 Dilithium 的實作更穩健。

## 進階：再往深一層

### Deterministic vs Hedged Signing

ECDSA 的教訓：PS3 用了固定的 k → private key 被算出來。ML-DSA 的做法是 **hedged signing**：`randomness = H(K || rnd || μ)`，結合確定性成分（K = 私鑰一部分）和真隨機數（rnd）。如果 RNG 壞了 → 退化成確定性簽章（仍安全）。如果 RNG 正常 → 額外防護 fault injection。永遠不會因為 RNG 問題洩漏私鑰。

### NIST 對 ML-DSA 的修改

FIPS 204 和 Round 3 Dilithium spec 有小差異（domain separation、key format、context string 支持）。兩者不兼容——用 FIPS 204 的 KAT 驗證實作。

### ML-DSA 在 TLS 中的部署影響

一個 ML-DSA-65 certificate ~5.5 KB（vs ECDSA 的 ~0.5 KB）。三層 certificate chain 全部用 ML-DSA → ~16.5 KB total。在 100 Mbps 連線上增加 ~1.2 ms，可接受。在 1 Mbps 的 IoT 連線上增加 ~120 ms，痛感明顯。

## 動手練習

1. **手算 Fiat-Shamir**：設定一個極簡的 Schnorr-like 簽章（q=23, g=5）。選 sk=7，計算 pk。選 y=3 作為遮罩，message="hi"。手算 commit → challenge（用 H(w‖M) mod 23 模擬）→ response。驗證 verify 能通過。

2. **Rejection sampling 統計**：修改範例一的程式碼，把 gamma1 從 30 降到 10，跑 1000 次 sign，統計每次簽章的平均嘗試次數。然後把 gamma1 改回 30，比較差異。解釋為什麼 gamma1 越大，成功率越高。

3. **Size 影響估算**：假設你的 TLS certificate chain 有 3 個 certificate（root CA → intermediate CA → leaf）。計算在以下三種情況下 handshake 增加的 bytes 數：
   - 全部用 ECDSA P-256
   - 全部用 ML-DSA-65
   - Hybrid：每個 cert 同時包含 ECDSA 和 ML-DSA 簽章

4. **比較 verify 速度**：用 Python 的 `timeit` 測量範例一中 sign() 和 verify() 的執行時間差異。驗證 verify 是否確實比 sign 快。

## 本章重點整理

- ML-DSA 用 Fiat-Shamir transform 把互動式 Module-LWE proof 變成非互動式數位簽章
- 簽章流程：commitment w = Ay → challenge c = H(w₁ ‖ M) → response z = y + c·s₁
- Rejection sampling 確保 z 不洩漏秘密 s₁ 的資訊，平均嘗試 4-7 次
- ML-DSA-65 的 pk = 1952B、sig = 3293B，比 Ed25519 大 50 倍以上，但 verify 更快
- Hedged signing 結合確定性和隨機性，避免因 RNG 故障洩漏私鑰
- 部署挑戰主要在 TLS certificate chain 的 size 膨脹，在 IoT 環境下尤其明顯

## 自我檢核

- [ ] 能解釋 Fiat-Shamir transform 如何把互動式證明變成非互動式簽章
- [ ] 能追蹤 ML-DSA 的 Sign 流程（commit → challenge → response）
- [ ] 能解釋 rejection sampling 的必要性（防止 z 洩漏 s₁ 的資訊）
- [ ] 能比較 ML-DSA-65 和 Ed25519 的 key size、sig size、verify speed
- [ ] 能解釋為什麼 ML-DSA 的 verify 比 sign 快
- [ ] 能說出 ML-DSA 在 TLS 部署中的主要挑戰
- [ ] 能區分 ML-DSA 和 FALCON 的取捨

## 延伸閱讀

- **NIST FIPS 204, "Module-Lattice-Based Digital Signature Standard"（2024）**
  - **讀哪裡**：Section 4–6（algorithm specification）
  - **學什麼**：ML-DSA 的完整規格，包括 HighBits/LowBits/MakeHint 的精確定義
  - **關聯**：本章簡化版的正式版本

- **Léo Ducas et al., "CRYSTALS-Dilithium Algorithm Specifications"（Round 3 Submission, 2021）**
  - **讀哪裡**：Section 2（design rationale）和 Section 5（rejection sampling analysis）
  - **學什麼**：為什麼選這些參數、rejection sampling 的成功機率分析
  - **關聯**：本章 rejection sampling 段落的理論來源

- **Amos Fiat & Adi Shamir, "How To Prove Yourself"（1986）**
  - **讀哪裡**：全文（只有 10 頁）
  - **學什麼**：Fiat-Shamir transform 的原始論文——把互動式 ID protocol 變成簽章
  - **關聯**：本章 Fiat-Shamir 段落的理論來源

- **Vadim Lyubashevsky, "Fiat-Shamir With Aborts"（2009）**
  - **讀哪裡**：Section 1–3
  - **學什麼**：rejection sampling（"aborts"）在 lattice-based 簽章中的理論基礎
  - **關聯**：本章 rejection sampling 的直接理論來源

→ [Ch 33 SLH-DSA (SPHINCS+)](./33-slh-dsa-sphincs.md)
