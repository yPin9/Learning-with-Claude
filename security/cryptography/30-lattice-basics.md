# Ch 30 — Lattice 基礎：LWE、Module-LWE、SVP / CVP

> 目標：從零教格密碼必要的數學：lattice 是什麼、shortest vector / closest vector problem、LWE（Learning With Errors）為什麼難、Module-LWE 的效能優勢，為什麼 NIST 最終選擇基於 lattice 的 ML-KEM / ML-DSA。

## Lattice 是什麼

**直覺**：lattice 是「整數線性組合」的集合。

```
2D lattice 例：
  basis vectors: b1 = (2, 0), b2 = (1, 2)
  lattice = { a × b1 + b × b2 : a, b ∈ ℤ }
  
  畫出來：
    □  □  □  □  □
       □  □  □  □
    □  □  □  □  □
       □  □  □  □
    □  □  □  □  □
```

正式：n 維空間中由 n 個 linearly independent vector `b_1, ..., b_n` 生成的整數組合：

```
L = { Σ a_i × b_i : a_i ∈ ℤ }
```

## Lattice 的 basis 不唯一

```
b1' = (3, 2) = b1 + b2
b2' = (1, 2) = b2

也是 lattice 的 basis（生成同一個 set）
```

**「好的 basis」**（向量短、互相接近正交）vs **「壞的 basis」**（向量長、扁）— 這個差異是 lattice 密碼學的關鍵。

## 兩個經典 hard problem

### SVP (Shortest Vector Problem)

```
給 lattice basis B
找 lattice 中最短的非零向量
```

對 random lattice，n 維（n ≥ 100）已知無 polynomial 算法。**指數複雜度**。

### CVP (Closest Vector Problem)

```
給 lattice basis B 與一個目標點 t
找 lattice 中最接近 t 的點
```

也是 NP-hard。

### approx-SVP / γ-SVP

放寬：**找一個比最短向量長 γ 倍以內的向量**。對小 γ 仍 hard。

LLL 算法（Lenstra-Lenstra-Lovász 1982）給 polynomial-time 解，但 γ 是 exponential（2^O(n)）— 對密碼學「mild」approximation。

## Babai's algorithm（解 CVP，給 good basis）

```
Babai_round(B, t):
  Solve B × x = t (linear algebra, get real x)
  Round each component of x to nearest integer
  Return B × round(x)
```

**有 good basis 時準確**；bad basis 時答案差很多。**密碼學用這個 trapdoor**：

- 公鑰 = bad basis（公開）
- 私鑰 = good basis（保密）
- 別人用 bad basis 解 CVP 困難
- 你用 good basis 解 CVP 簡單

GGH cryptosystem (1997) 用這個直接構造，但**不夠安全**（被攻破）。現代用 LWE。

## LWE：Learning With Errors

Regev 2005 提出。**現代 lattice 密碼的核心**。

```
給 secret s ∈ Z_q^n
產生樣本 (a, b) where:
  a ← Z_q^n random
  e ← small error
  b = <a, s> + e (mod q)

LWE problem:
  Distinguish:
    - LWE samples: (a_i, b_i = <a_i, s> + e_i)
    - Random:      (a_i, u_i) random
  
  Equivalently: 從多個樣本還原 s
```

直覺：每個樣本是「s 的 noisy linear measurement」。**沒 noise 時普通 linear algebra 解出 s（高斯消去法）**。**有 noise** → 變成 lattice CVP 問題（n 維中找最近 lattice 點）。

對 random q（多項式 polynomial 大小，e.g., q ≈ n²），LWE = 用 secret s 解 average-case lattice problem。Regev 證明 average-case = worst-case（**世界級 reduction**）。

## LWE 與 SVP/CVP 的關係

Regev 的證明：

```
Solving LWE on average → solving worst-case GapSVP (approx)
```

意思：**如果你能在 average 情況解 LWE，就能解任何 lattice 的 SVP**。**lattice SVP 假設困難 → LWE 安全**。

這個 reduction 是 lattice 密碼學的數學支柱。**比 RSA 的「factor n hard」更穩**（後者只是 average case 假設）。

## Ring-LWE：效能優化

純 LWE：**key 大**（n × m 矩陣）+ **計算慢**（matrix-vector 乘法）。

Ring-LWE：把 vector 換成 polynomial ring `R_q = Z_q[x] / (x^n + 1)`：

```
secret s ∈ R_q
sample (a, b) where:
  a ← R_q random
  e ← small error in R_q
  b = a × s + e (in R_q)
```

ring multiplication 用 NTT (Number Theoretic Transform，類似 FFT) → O(n log n)。

優點：

- key 從 O(n²) 縮到 O(n)
- multiplication 從 O(n²) 到 O(n log n)
- 仍有 worst-case reduction（但只到「ideal lattice」這個 sub-class）

NewHope（2015）等早期 lattice 算法用 Ring-LWE。

## Module-LWE：折衷

Ring-LWE 有風險：**ideal lattice 可能比 general lattice 弱**。Module-LWE 是中間：

```
secret s ∈ R_q^k （k 個 ring element 的 vector）
sample (A, b) where:
  A ∈ R_q^(k×k)
  e ← small error in R_q^k
  b = A × s + e
```

k = 1 退化成 Ring-LWE；k = n 退化成 plain LWE。實務 k = 2, 3, 4。

**Module-LWE 的優勢**：

- 比 plain LWE 快、key 小
- 比 Ring-LWE 安全 margin 多
- **參數靈活**（同 ring 不同 k 給不同安全等級，code 可重用）

Kyber 用 Module-LWE。**這是 NIST 選 Kyber 的核心理由之一**。

## SIS：對偶問題

```
Short Integer Solution:
  給 random matrix A ∈ Z_q^(n × m)
  找 short non-zero vector x s.t. A × x = 0 (mod q)
```

LWE 的對偶。Lattice 簽章（Dilithium）用 SIS-based。

## NTRU：另一條 lattice 路

1996 Hoffstein-Pipher-Silverman 設計。基於 NTRU lattice：

```
公鑰：h = f^-1 × g (in some ring)
私鑰：(f, g) — 短多項式
```

NTRU 不靠 LWE/SIS reduction，純啟發式。**性能極好**（Encrypt 比 ECC 快）但安全證明弱。

NTRU 是 NIST PQC 第三輪 finalist 但沒選（lattice 多元化考量，且 NTRU 專利問題複雜過）。

## Why Lattice Won NIST

NIST 最終選 lattice-based（Kyber、Dilithium）作為主流：

1. **數學基礎強**：worst-case to average-case reduction
2. **性能好**：比 code-based / hash-based 都快
3. **key size 合理**：1KB 級（vs Classic McEliece 的 1MB）
4. **30+ 年研究**：相對成熟（vs isogeny 仍年輕）
5. **彈性**：encryption / signature / FHE / ZK 都能做

風險：**lattice 突破會殺光所有 lattice 算法**。所以 NIST 仍研究 code-based / hash-based 作為 backup。

## 簡單 LWE 例子（Python）

```python
import numpy as np

n = 4         # secret 維度
q = 17        # modulus（質數）
m = 8         # 樣本數

s = np.random.randint(0, q, n)
print(f"secret s = {s}")

# 產生 LWE samples
A = np.random.randint(0, q, (m, n))
e = np.array([np.random.randint(-1, 2) for _ in range(m)])  # small noise
b = (A @ s + e) % q

print(f"A:\n{A}")
print(f"b: {b}")

# 沒 noise 直接解：A @ s = b → 線性代數
# 有 noise：lattice CVP 問題（小 n 還能 BKZ 解）
```

n = 4 玩具，沒安全。Kyber 用 n = 256（每 polynomial 256 coefficients）+ k = 2/3/4。

## 一個常見誤解

「Lattice 我看不懂，我等 ECC 死再說」

**現在就要學**。Lattice 是 PQC 的事實標準（90% PQC 算法基於 lattice）。等 quantum 來才學 → 同事 / 比賽 / 職涯都晚了。

且 lattice 入門其實**比 ECC 簡單**：linear algebra 就夠。深入細節（NTT、reduction proof）才難。**入門理解 LWE 一週夠**。

## 自我檢核

- [ ] 我能畫 2D lattice 與 basis
- [ ] 我能解釋 SVP 與 CVP 的差別
- [ ] 我能寫 LWE 樣本生成
- [ ] 我能說出 LWE 與 lattice CVP 的 reduction 關係
- [ ] 我能比較 LWE / Ring-LWE / Module-LWE 的取捨
- [ ] 我能說出為什麼 NIST 選 lattice 為主

下一章看 ML-KEM (Kyber) 完整解剖。

→ [Ch 31 ML-KEM (Kyber)](./31-ml-kem-kyber.md)
