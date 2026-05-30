# Ch 30 — Lattice 基礎：LWE、Module-LWE、與格密碼的數學

> **目標**：能定義 lattice（格）、SVP / CVP，理解 LWE（Learning With Errors）和 Module-LWE 的數學結構，用 SageMath 建一個 LWE instance 來體驗「加了噪音的線性方程組」為什麼難解，知道 NIST 為什麼選格密碼作為 PQC 主力。

## 為什麼需要這個？

Ch 29 告訴你 Shor's algorithm 打爆了 RSA / DH / ECC。NIST 的應對方案是找一個「即使量子電腦也解不了」的數學難題來取代 factoring 和 DLP。

他們選了 **lattice（格）問題**。

NIST PQC 的三個標準裡，兩個（ML-KEM 和 ML-DSA）都基於 Module-LWE——一個格問題的變體。你在 Ch 31 和 Ch 32 要追蹤 Kyber 和 Dilithium 的完整流程，前提是理解 LWE 在做什麼。

這一章是數學深挖章。讀完之後，你會知道：
- 為什麼「加了噪音的線性方程組」在數學上是困難的
- LWE 和 lattice 問題之間的關係
- 為什麼 NIST 選了 lattice 而不是 code-based 或 isogeny-based

## 先建立直覺

想像一個遊戲：

```
遊戲一：沒有噪音的線性方程組
  3x + 5y = 23  (mod 97)
  7x + 2y = 58  (mod 97)

  → Gaussian elimination 秒解：x = 4, y = ？
  → 任何線性代數課本裡的標準操作

遊戲二：加了噪音的線性方程組（LWE）
  3x + 5y ≈ 23  (mod 97)    ← "≈" 表示答案可能差 ±1 或 ±2
  7x + 2y ≈ 58  (mod 97)
  1x + 9y ≈ 41  (mod 97)
  ...（很多方程式，但每個都有小誤差）

  → Gaussian elimination 失效（因為等號不精確）
  → 沒有已知的多項式時間演算法能解
  → 即使量子電腦也解不了（至少目前沒有演算法）
```

核心直覺：**LWE 就是「加了噪音的線性方程組」。沒有噪音時是小學數學；有噪音時變成目前數學界最困難的問題之一。**

這個觀察來自 Oded Regev（2005），他證明了 LWE 的困難性可以歸結到 lattice 問題的困難性。

## 核心概念：Lattice 的定義

### 什麼是 Lattice？

**Lattice（格）** 是 n 維空間中，由一組基底向量（basis vectors）的整數線性組合生成的離散點集合。

```
數學定義：
  給定線性獨立的向量 b₁, b₂, ..., bₙ ∈ ℝⁿ
  Lattice L = { a₁b₁ + a₂b₂ + ... + aₙbₙ | aᵢ ∈ ℤ }

二維範例（容易畫）：
  b₁ = (1, 0)
  b₂ = (0, 1)
  → L = ℤ²（所有整數座標的點）

  b₁ = (2, 1)
  b₂ = (1, 3)
  → L = { a(2,1) + b(1,3) | a,b ∈ ℤ }
```

用 ASCII 圖來看二維 lattice：

```
  y
  ↑
  6 ·         ·         ·         ·
  5    ·         ·         ·
  4 ·         ·         ·         ·
  3    ·      [b₂]·        ·
  2 ·         ·         ·         ·
  1    · [b₁]    ·         ·
  0 ○─────────────────────────────→ x
  0  1  2  3  4  5  6  7  8

  b₁ = (2, 1), b₂ = (1, 3)
  ○ = 原點
  · = lattice 點
```

關鍵觀察：同一個 lattice 可以有不同的基底（basis）。有些基底的向量短且接近正交（"好" 基底），有些向量長且方向接近（"壞" 基底）。

### 範例一：用 SageMath 建立和視覺化 Lattice

```python
# SageMath 環境
# 定義一個二維 lattice
B = matrix(ZZ, [[2, 1], [1, 3]])
L = IntegerLattice(B)

print("Basis:")
print(B)
print(f"\nDeterminant (volume of fundamental domain): {L.determinant()}")
print(f"Dimension: {L.dimension()}")

# 列舉 lattice 中 norm ≤ 5 的點
from sage.modules.free_module_integer import IntegerLattice
short_vectors = L.short_vectors(26)  # norm² ≤ 26
for norm_sq, vectors in short_vectors:
    if norm_sq > 0:
        print(f"  norm² = {norm_sq}: {vectors}")

# 找最短非零向量（SVP 的解）
svp = L.shortest_vector()
print(f"\nShortest vector (SVP solution): {svp}")
print(f"  norm = {svp.norm().n():.4f}")
```

如果沒有 SageMath，可以用 NumPy 暴力列舉低維 lattice 點來找最短向量。但在高維度（n > 40），暴力列舉完全不可行——這正是 lattice 問題困難的原因。

## 底層機制：SVP、CVP、與困難性

### SVP（Shortest Vector Problem，最短向量問題）

```
給定：lattice L 的一組基底 B
目標：找到 L 中最短的非零向量 v（‖v‖ 最小）

          y
          ↑        · 
          |     ·     ·
          |  ·  [v]·     ·
          | ·  ↗  ·     ·
          ○─────────────→ x
         ↑ 原點

  v 是離原點最近的 lattice 點
  二維容易找，n > 300 維極其困難
```

### CVP（Closest Vector Problem，最近向量問題）

```
給定：lattice L 的基底 B，和一個目標點 t（不在 L 上）
目標：找到 L 中離 t 最近的點

          y
          ↑        · 
          |     ·   ★ ·      ★ = 目標點 t
          |  ·     ·  ←·     ← = 最近的 lattice 點
          | ·     ·     ·
          ○─────────────→ x

  找離 ★ 最近的 · 點
```

### 為什麼這些問題困難？

```
維度 vs 難度：

維度 n    │ SVP 精確解的已知最佳演算法（經典）  │ 時間
──────────┼────────────────────────────────────┼────────────
    2     │ Gauss reduction                     │ O(1)
   50     │ BKZ                                 │ 秒
  100     │ BKZ-2.0                             │ 分鐘
  200     │ BKZ + 篩法                          │ 小時～天
  400     │ 理論上可解，實務上已經很痛苦          │ 年
  500+    │ 超出目前計算能力                     │ ??

最佳已知演算法：2^{O(n)} 時間
量子電腦：不能做得顯著更好（不像 factoring 有 Shor）

這就是為什麼 lattice 問題適合做 PQC 的基礎：
  - factoring: 經典困難，量子容易（Shor）
  - lattice SVP: 經典困難，量子也困難
```

### LWE（Learning With Errors）

Oded Regev（2005）定義的問題：

```
LWE 問題定義：
  公開參數：n（維度）, q（模數）, χ（誤差分布，通常是小的離散 Gaussian）
  
  秘密：向量 s ∈ ℤqⁿ（隨機選的）
  
  攻擊者看到的：
    (a₁, b₁ = ⟨a₁, s⟩ + e₁  mod q)
    (a₂, b₂ = ⟨a₂, s⟩ + e₂  mod q)
    ...
    (aₘ, bₘ = ⟨aₘ, s⟩ + eₘ  mod q)
  
  其中：
    aᵢ ∈ ℤqⁿ  是隨機的（公開）
    eᵢ ← χ    是小的誤差（秘密）
    bᵢ ∈ ℤq   是觀測值（公開）
  
  目標：從 {(aᵢ, bᵢ)} 恢復 s

用矩陣寫：
  A ∈ ℤqᵐˣⁿ（隨機公開矩陣）
  s ∈ ℤqⁿ  （秘密向量）
  e ∈ ℤqᵐ  （小誤差向量）
  b = As + e  (mod q)

  知道 A 和 b，求 s
```

為什麼沒有噪音就能解，有噪音就不能解？

```
沒有噪音（e = 0）：
  b = As  (mod q)
  → 標準線性方程組
  → Gaussian elimination: O(n³)

有噪音（e ≠ 0，但每個 eᵢ 很小）：
  b = As + e  (mod q)
  → Gaussian elimination 算出的 s 是錯的（噪音污染了方程組）
  → 沒有已知的多項式時間演算法
  → Regev 證明：解 LWE 至少和解 lattice 上的某些問題一樣困難
```

### LWE → Lattice 的歸結（直覺版）

```
LWE instance: (A, b = As + e)

構造一個 lattice：
  L = { x ∈ ℤᵐ | x = Ay mod q, 對某個 y ∈ ℤⁿ }

b = As + e 意味著 b 離 lattice 很近（因為 As 在 L 上，e 很小）

恢復 s ≈ 在 L 上找離 b 最近的點（CVP！）

所以：解 LWE → 解 CVP → 至少和 SVP 一樣困難

而 SVP 在高維度上，即使量子電腦也沒有已知的多項式時間演算法
```

### 範例：用 SageMath 建立 LWE Instance

```python
# SageMath 環境
n, m, q = 4, 8, 97
F = GF(q)

set_random_seed(42)
s = vector(F, [F.random_element() for _ in range(n)])
A = matrix(F, m, n, [F.random_element() for _ in range(m * n)])

from sage.stats.distributions.discrete_gaussian_integer import DiscreteGaussianDistributionIntegerSampler
D = DiscreteGaussianDistributionIntegerSampler(sigma=2.0)
e = vector(F, [F(D()) for _ in range(m)])

b = A * s + e

# 沒有噪音：Gaussian elimination 秒解
b_clean = A * s
s_recovered = A[:n].solve_right(b_clean[:n])
print(f"Without noise: s = {s_recovered}, correct = {s_recovered == s}")  # True

# 有噪音：解出來是錯的
try:
    s_wrong = A[:n].solve_right(b[:n])
    print(f"With noise: s = {s_wrong}, correct = {s_wrong == s}")  # False
except ValueError:
    print("No solution (noise makes system inconsistent)")

# 噪音 e 的每個元素只有 ±2 左右，但這足以讓 Gaussian elimination 失效
# 在真正的 Kyber 參數（n=256, q=3329）下，沒有已知演算法能解
```

## 進一步用法：Module-LWE 和 Ring-LWE

### 為什麼需要 Module-LWE？

原始 LWE 的問題：public key 太大。

```
LWE 的 key size：
  矩陣 A ∈ ℤqⁿˣⁿ
  如果 n = 256, q = 3329 → A 有 256² = 65,536 個元素
  每個元素 ~12 bits → 公鑰 ~96 KB（太大了！）

解法一：Ring-LWE（NTRU 系列）
  把 ℤq 上的向量換成多項式環 Rq = ℤq[X]/(X^n + 1) 上的元素
  一個多項式 = n 個係數 → 相當於一個 n 維向量
  但多項式乘法有結構 → key 變小，但安全假設更強

解法二：Module-LWE（Kyber / Dilithium 用的）
  取 Ring-LWE 的小向量版本
  用 k×k 的多項式矩陣（k 很小，如 2、3、4）
  每個矩陣元素是 Rq 的一個多項式

  兼顧：比 LWE key 小很多，比 Ring-LWE 安全假設保守
```

```
Module-LWE 的結構：

  Rq = ℤq[X]/(X^n + 1)   ← 多項式環，n = 256, q = 3329

  矩陣 A ∈ Rq^{k×k}       ← k×k 的多項式矩陣（k = 2,3,4）
  秘密 s ∈ Rq^k            ← k 個多項式組成的向量
  誤差 e ∈ Rq^k            ← k 個小多項式

  b = As + e  (in Rq^k)

  公鑰 = (A, b)
  私鑰 = s

  key size：
    k=2 (Kyber-512):  pk = 800 bytes
    k=3 (Kyber-768):  pk = 1184 bytes
    k=4 (Kyber-1024): pk = 1568 bytes

  比起原始 LWE 的 ~96 KB，小了 60-120 倍
```

### 範例二：Module-LWE 的多項式環 Rq

```python
"""
在 Rq = Zq[X]/(X^n + 1) 上做多項式運算
這是 Kyber / Dilithium 的基礎代數結構
"""
import numpy as np

class PolyRq:
    """Rq = Zq[X]/(X^n + 1) 上的多項式"""
    
    def __init__(self, coeffs, n=256, q=3329):
        self.n = n
        self.q = q
        # 確保長度為 n，mod q
        self.coeffs = np.array(coeffs[:n], dtype=np.int64) % q
        if len(self.coeffs) < n:
            self.coeffs = np.pad(self.coeffs, (0, n - len(self.coeffs)))
    
    def __add__(self, other):
        return PolyRq((self.coeffs + other.coeffs) % self.q, self.n, self.q)
    
    def __sub__(self, other):
        return PolyRq((self.coeffs - other.coeffs) % self.q, self.n, self.q)
    
    def __mul__(self, other):
        """多項式乘法 mod (X^n + 1) mod q — 樸素 O(n²) 版本"""
        result = np.zeros(2 * self.n - 1, dtype=np.int64)
        for i in range(self.n):
            for j in range(self.n):
                result[i + j] = (result[i + j] + 
                    int(self.coeffs[i]) * int(other.coeffs[j])) % self.q
        
        # 模 X^n + 1：X^n ≡ -1，所以高次項折回去要取負
        reduced = np.zeros(self.n, dtype=np.int64)
        for i in range(2 * self.n - 1):
            idx = i % self.n
            sign = 1 if (i // self.n) % 2 == 0 else -1
            reduced[idx] = (reduced[idx] + sign * result[i]) % self.q
        
        return PolyRq(reduced, self.n, self.q)
    
# 示範：小參數
n, q = 8, 97
rng = np.random.default_rng(42)
a = PolyRq(rng.integers(0, q, size=n), n, q)
s = PolyRq(rng.integers(-2, 3, size=n), n, q)  # 小秘密
e = PolyRq(rng.integers(-1, 2, size=n), n, q)   # 小誤差

b = a * s + e  # LWE: b = a*s + e
# 公鑰: (a, b), 私鑰: s
```

## 對比與取捨

| 特性 | Lattice-based | Code-based | Isogeny-based | Hash-based |
|---|---|---|---|---|
| 代表方案 | Kyber, Dilithium | Classic McEliece | SIKE（已破） | SPHINCS+ |
| 安全假設 | SVP / LWE | syndrome decoding | SIDH isogeny | hash collision |
| 假設歷史 | ~20 年 | ~45 年 | ~10 年（已被攻破）| ~30 年 |
| 量子安全 | ✓（目前認為）| ✓ | ✗（經典攻擊打穿）| ✓ |
| public key 大小 | 小（~800B–1.5KB）| 巨大（~260KB–1MB）| 極小（~200B）| 中（~32–64B）|
| ciphertext / sig 大小 | 中 | 中 | 極小 | 大（~8–49KB）|
| 功能多樣性 | KEM + 簽章 + FHE | 主要 KEM | KEM | 只有簽章 |
| NIST 選為標準 | ✓（主力）| Round 4 候選 | ✗ | ✓（備用）|
| 為什麼 NIST 選它 | key 小、速度快、多用途 | — | — | 安全假設保守 |

**為什麼 Lattice 贏了？** 功能多樣性（一套數學做 KEM + 簽章）、key/ciphertext 大小平衡（Kyber-768 pk = 1184B vs Classic McEliece 的 ~1MB）、速度和 X25519 相當（NTT 加速）、以及 20 年無重大密碼分析突破。但仍然比 factoring 的 50 年歷史短——這是 NIST 保留 SPHINCS+ 作為備用的原因。

## 踩雷集錦

1. **「LWE 就是加噪音的矩陣——可以用統計方法去噪」**：LWE 的噪音不是高斯白噪音那種可以用信號處理濾掉的東西。在 mod q 的有限域上，噪音和信號混在一起，沒有平滑的連續結構可以利用。去噪的最佳方法等價於解 SVP，而 SVP 是困難的。

2. **「維度 n 越大越安全，所以用 n=1024 最保險」**：安全性取決於 n、q、和誤差分布 χ 的組合。n 大但 q 也大、或 χ 太寬，安全性不一定增加。Kyber 的參數是 NIST 精心調整過的——不要自己改。

3. **「Ring-LWE 比 Module-LWE 快，應該用 Ring-LWE」**：Ring-LWE 的環結構提供了更多數學結構給攻擊者利用。Module-LWE 是 Ring-LWE 和 LWE 之間的折衷——有足夠的結構來加速運算，但不像 Ring-LWE 那樣給攻擊者太多可利用的結構。Kyber 和 Dilithium 用 Module-LWE 是有意識的安全 / 效能取捨。

4. **「Lattice 問題從來沒被攻破過」**：不精確。特定參數的 lattice 問題被解決過——例如 lattice dimension 太小的情況下，BKZ 演算法能有效求解。安全的關鍵是參數要大到 BKZ 的運行時間超過 2^128。NIST 的參數選擇保證了這點。

5. **「SIKE 被攻破說明 PQC 整體不可靠」**：SIKE 基於 isogeny 問題，和 lattice 是完全不同的數學結構。SIKE 的失敗不影響 lattice-based 方案的安全性。但它確實是一個警示：數學假設需要更長時間的審查，這也是 NIST 保留 hash-based SPHINCS+ 作為備用的原因。

## 進階：再往深一層

### Lattice Reduction 演算法

攻擊 lattice 問題的主要工具是 **lattice reduction**——把一組 "壞" 的基底轉換成 "好" 的（向量更短、更正交）。

```
LLL 演算法（Lenstra–Lenstra–Lovász, 1982）：
  - 多項式時間：O(n⁵ log³ B)
  - 找到的向量不是最短的，但不會太長
  - 保證：‖v‖ ≤ 2^{(n-1)/2} · λ₁（λ₁ = 真正最短向量的長度）
  - 在低維度（n < 40）非常有效

BKZ 演算法（Block Korkine-Zolotarev）：
  - 比 LLL 更強，但不是多項式時間
  - 參數 β（block size）控制品質 vs 時間的取捨
  - β = 2: 等價於 LLL
  - β 越大 → 找到的向量越短 → 時間指數增長
  - 現在最好的實作：BKZ 2.0 + progressive sieving

Kyber 安全性估計：
  攻擊者需要用 BKZ with β ≈ 400+ 才能打破 Kyber-512
  這對應 2^128+ 次操作（足夠安全）
```

### Decision-LWE vs Search-LWE

```
Search-LWE：給定 (A, b = As + e)，恢復 s
Decision-LWE：區分 (A, b = As + e) 和 (A, b = random)

Regev 證明這兩個問題一樣困難（在 q = poly(n) 的情況下）

Decision-LWE 在密碼學中更常用：
  - 加密：把明文藏在「看起來像隨機」的 LWE 樣本裡
  - 如果攻擊者不能區分 LWE 和隨機 → 看不出明文
```

### Worst-Case to Average-Case Reduction

Regev 的核心貢獻不只是定義 LWE，而是證明：

```
如果你能解 average-case LWE（隨機的 A、隨機的 s）
→ 你就能解 worst-case lattice 問題（任意的 lattice 上的 GapSVP）

這意味著：
  - LWE 的安全性不依賴「挑了一個特別困難的 instance」
  - 隨機生成的 LWE instance 平均來說就很困難
  - 這是 factoring / DLP 沒有的性質（RSA 的安全性依賴於你挑到了好的 p, q）
```

## 動手練習

1. **手算 LWE**：在 n=2, q=17 的設定下，秘密 s = (3, 5)，誤差 ≤ 1。手算以下 LWE 樣本的 b 值：
   - a₁ = (2, 7), e₁ = 1
   - a₂ = (5, 3), e₂ = -1
   - a₃ = (1, 11), e₃ = 0
   然後假裝你不知道 s，嘗試從 (aᵢ, bᵢ) 恢復 s。

2. **SageMath 實驗**：用 SageMath 生成 n=50, q=97 的 LWE instance，嘗試用 `matrix.solve_right()` 直接解（預期失敗）。把 error_bound 設成 0 再試一次（預期成功）。記錄兩次的結果差異。

3. **BKZ 體驗**：用 fpylll（Python 的 lattice reduction 庫）對一個 40 維 lattice 跑 LLL 和 BKZ-20，比較找到的最短向量長度。

4. **Key size 計算**：計算 Module-LWE with n=256, q=3329, k=2 的公鑰大小（bytes）。和 RSA-2048 的公鑰比較。

## 本章重點整理

- Lattice 是由基底向量的整數線性組合生成的離散點集合；SVP 和 CVP 是 lattice 上的核心困難問題
- LWE = 加了噪音的線性方程組：沒有噪音是線性代數（秒解），有噪音是 NP-hard 等級的困難
- Regev（2005）證明 LWE 的 average-case 困難性可以歸結到 lattice 問題的 worst-case 困難性
- Module-LWE 是 Kyber / Dilithium 使用的 LWE 變體，在多項式環 Rq = ℤq[X]/(X^n+1) 上運作，兼顧安全性和效能
- NIST 選 lattice 做 PQC 主力的原因：key 小、速度快、功能多樣（KEM + 簽章）、20 年無重大攻破

## 自我檢核

- [ ] 能定義 lattice 並畫出二維範例
- [ ] 能區分 SVP 和 CVP，並解釋為什麼它們困難
- [ ] 能用一句話解釋 LWE 的核心直覺（加了噪音的線性方程組）
- [ ] 能寫出 LWE 的數學形式：b = As + e (mod q)
- [ ] 能解釋 Module-LWE 和原始 LWE 的關係，以及為什麼 Module-LWE 更實用
- [ ] 能說出 NIST 選 lattice-based 方案的至少三個原因
- [ ] 能解釋 SIKE 被攻破為什麼不影響 lattice-based 方案的安全性

## 延伸閱讀

- **Oded Regev, "On Lattices, Learning with Errors, Random Linear Codes, and Cryptography"（2005, STOC）**
  - **讀哪裡**：Section 1（Introduction）和 Section 3（LWE definition）
  - **學什麼**：LWE 的原始定義和 worst-case to average-case reduction 的直覺
  - **關聯**：本章 LWE 部分的理論來源

- **Chris Peikert, "A Decade of Lattice Cryptography"（2016, survey）**
  - **讀哪裡**：Section 1–3（overview + lattice basics + LWE）
  - **學什麼**：lattice crypto 10 年發展的全景圖，寫得清晰
  - **關聯**：本章所有概念的更深入版本

- **Daniele Micciancio & Oded Regev, "Lattice-based Cryptography" in Post-Quantum Cryptography（2009, book chapter）**
  - **讀哪裡**：Section 1–4
  - **學什麼**：lattice 問題的困難性層次結構（SVP → CVP → GapSVP → LWE）
  - **關聯**：本章進階段落的理論背景

- **NIST PQC Round 3 Report（2022）**
  - **讀哪裡**：Section 4（Selection rationale for CRYSTALS-Kyber 和 CRYSTALS-Dilithium）
  - **學什麼**：NIST 為什麼選了 lattice 而不是其他方案家族
  - **關聯**：本章對比表的決策邏輯

→ [Ch 31 ML-KEM (Kyber)](./31-ml-kem-kyber.md)
