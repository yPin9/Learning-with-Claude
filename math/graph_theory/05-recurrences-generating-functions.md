# Ch 5 — 遞迴關係與生成函數

> **目標**：會從組合結構寫出遞迴關係並解出封閉形式；理解普通生成函數（OGF）與指數生成函數（EGF）如何把「組合結構的拼接」翻譯成「冪級數的乘法」；最後用 EGF 的 log 算出 n 點連通標號圖的數量，並和暴力窮舉對上。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。冪級數運算用標準函式庫的 `fractions.Fraction` 做精確有理數計算。

## 為什麼需要這個？

[Ch 3](./03-counting-basics.md) 和 [Ch 4](./04-inclusion-exclusion.md) 給你的是「一個公式算一個數」。但很多計數問題沒有現成公式，只有一個**遞迴結構**：

- 一棵二元樹 = 根 + 左子樹 + 右子樹。n 個節點的二元樹有幾種？
- 一張圖 = 若干個連通元件的集合。n 點的連通標號圖有幾張？
- 一條路徑圖上的獨立集：最後一個點選或不選，剩下的是更短的路徑。

這類問題你在 DP 裡天天寫遞迴，然後填表。填表能給你 n = 100 的答案，但給不了兩件事：**封閉形式**（`C(2n, n)/(n+1)` 這種一行公式），和**成長速度**（是 `2^n`、`4^n / n^1.5`、還是 `n^n`）。成長速度決定了一個暴力演算法在 n = 30 時是跑一秒還是跑到宇宙熱寂。

生成函數是解決這兩件事的系統方法。核心想法：把整個數列 `a_0, a_1, a_2, ...` 打包成一個冪級數 `A(x) = Σ a_n x^n`，然後讓**組合結構的拼接**對應到**冪級數的代數運算**。結構寫成方程式，方程式解出 A(x)，再把係數讀回來。

## 先建立直覺

把生成函數想成一條**晾衣繩**，第 n 個夾子上掛著 `a_n`：

```text
A(x) =  a_0  +  a_1 x  +  a_2 x^2  +  a_3 x^3  + ...
        夾子0   夾子1     夾子2       夾子3
```

x 本身沒有意義，它只是用指數標記「這是第幾個夾子」。這樣做的好處在乘法：

```text
(a_0 + a_1 x + a_2 x^2 + ...)(b_0 + b_1 x + b_2 x^2 + ...)

x^n 的係數 = a_0 b_n + a_1 b_(n-1) + ... + a_n b_0
```

如果 `a_k` 是「大小為 k 的 A 結構數」、`b_k` 是「大小為 k 的 B 結構數」，右邊正好是「把一個 A 結構和一個 B 結構接起來、總大小為 n」的方法數。**乘法 = 拼接**，這是整章最重要的一句話。

> 本章大量用到 Ch 3 的二項式係數與 Ch 4 的錯排；DP 的遞迴寫法可參考 [leetcode_patterns 的 DP 基礎](../../algorithms/leetcode_patterns/24-dp-fundamentals.md)。

## 線性遞迴：路徑圖上的獨立集

**獨立集（independent set）** 是兩兩不相鄰的頂點子集。路徑圖 `P_n`（頂點 `1 - 2 - ... - n`）的獨立集，等價於「長度 n、沒有兩個相鄰 1 的 0/1 字串」（第 i 位是 1 代表選第 i 個點）。令 `a(n)` 是它的數量。

**遞迴**：看第 n 個點。

- 不選：前 n − 1 個點任意構成獨立集，`a(n − 1)` 種。
- 選：第 n − 1 個點不能選，前 n − 2 個點任意，`a(n − 2)` 種。

所以 `a(n) = a(n − 1) + a(n − 2)`，`a(0) = 1`（空集合）、`a(1) = 2`。這是 Fibonacci 遞迴，`a(n) = F(n + 2)`（`F(1) = F(2) = 1`）。

### 特徵方程式

**定理 5.1**：若 `a(n) = c1 a(n−1) + c2 a(n−2)`，且特徵方程式 `r^2 = c1 r + c2` 有兩個相異根 r1、r2，則存在常數 α、β 使得對所有 n，`a(n) = α r1^n + β r2^n`。

**證明**：先驗證 `r^n` 滿足遞迴若且唯若 r 是特徵根：`r^n = c1 r^(n−1) + c2 r^(n−2)` 除以 `r^(n−2)` 就是特徵方程式（r ≠ 0 時；若 c2 ≠ 0 則 0 不是根）。遞迴是線性的，所以 `α r1^n + β r2^n` 也滿足遞迴。再選 α、β 讓它符合初始值：

```text
α + β = a(0)
α r1 + β r2 = a(1)
```

係數矩陣的行列式是 `r2 − r1 ≠ 0`，所以有唯一解。兩個數列滿足同一個二階遞迴且前兩項相同，由歸納法每一項都相同。證畢。

對 `a(n) = a(n−1) + a(n−2)`：`r^2 = r + 1`，`r = φ = (1 + √5)/2` 或 `ψ = (1 − √5)/2`。解出常數得到 Binet 公式 `F(n) = (φ^n − ψ^n)/√5`。因為 `|ψ| < 1`，第二項很快趨近 0，所以 `F(n)` 成長得像 `φ^n / √5 ≈ 1.618^n`。

```python
"""路徑圖 P_n 的獨立集數 = 不含相鄰 1 的 n 位元字串數：暴力、遞迴、Binet。"""
from itertools import product
from math import sqrt


def brute(n):
    return sum(1 for bits in product((0, 1), repeat=n)
               if all(not (bits[i] and bits[i + 1]) for i in range(n - 1)))


def rec(n):
    a, b = 1, 2          # a(0)=1（空字串）, a(1)=2
    if n == 0:
        return a
    for _ in range(n - 1):
        a, b = b, a + b
    return b


phi, psi = (1 + sqrt(5)) / 2, (1 - sqrt(5)) / 2


def binet(n):            # a(n) = F(n+2)
    return round((phi ** (n + 2) - psi ** (n + 2)) / sqrt(5))


for n in range(0, 9):
    print(f"n={n:>2}: 暴力 {brute(n):>4}  遞迴 {rec(n):>4}  Binet {binet(n):>4}")
first = next(n for n in range(200) if binet(n) != rec(n))
print("Binet 第一次出錯: n =", first)
for n in (first, 75, 80):
    print(f"n={n}: 遞迴 {rec(n)}  Binet {binet(n)}  差 {binet(n) - rec(n)}")
```

輸出：

```text
n= 0: 暴力    1  遞迴    1  Binet    1
n= 1: 暴力    2  遞迴    2  Binet    2
n= 2: 暴力    3  遞迴    3  Binet    3
n= 3: 暴力    5  遞迴    5  Binet    5
n= 4: 暴力    8  遞迴    8  Binet    8
n= 5: 暴力   13  遞迴   13  Binet   13
n= 6: 暴力   21  遞迴   21  Binet   21
n= 7: 暴力   34  遞迴   34  Binet   34
n= 8: 暴力   55  遞迴   55  Binet   55
Binet 第一次出錯: n = 69
n=69: 遞迴 308061521170129  Binet 308061521170130  差 1
n=75: 遞迴 5527939700884757  Binet 5527939700884771  差 14
n=80: 遞迴 61305790721611591  Binet 61305790721611752  差 161
```

Binet 公式數學上完全正確，但 float64 的 φ 只有約 16 位有效數字，這個相對誤差在 `φ^k` 裡被放大約 k 倍。`F(71) ≈ 3 × 10^14` 時，絕對誤差已經到了 0.5 的量級，所以 `n = 69`（也就是 `F(71)`）開始取整出錯，之後誤差越滾越大。封閉形式的價值在**分析**（成長速度、漸近行為），不在**計算**；要精確值就用整數遞迴。

## 普通生成函數（OGF）

**定義**：數列 `a_0, a_1, ...` 的普通生成函數是**形式冪級數（formal power series）** `A(x) = Σ_{n≥0} a_n x^n`。

「形式」的意思是：我們**不**把 x 代入數字，也不問級數收不收斂。冪級數只是一種資料結構，加法逐項加，乘法做卷積。這讓所有代數操作都合法，即使係數長得像 `n!` 那樣、級數在任何 x ≠ 0 都發散。

### 用 OGF 解遞迴

對 `a(n) = a(n−1) + a(n−2)`、`a(0) = 1`、`a(1) = 2`，令 `A(x) = Σ a_n x^n`：

```text
A(x) − a_0 − a_1 x = Σ_{n≥2} a_n x^n
                   = Σ_{n≥2} a_{n−1} x^n + Σ_{n≥2} a_{n−2} x^n
                   = x (A(x) − a_0) + x^2 A(x)
```

代入初始值 `A − 1 − 2x = x(A − 1) + x^2 A`，整理得

```text
A(x) = (1 + x) / (1 − x − x^2)
```

分母 `1 − x − x^2 = (1 − φx)(1 − ψx)`，部分分式展開後每一項都是幾何級數 `1/(1 − r x) = Σ r^n x^n`，就回到 Binet 公式。OGF 把「解遞迴」變成「做部分分式」，對任何常係數線性遞迴都適用，包括特徵根重複的情況（重根會給出 `n r^n` 這種項，定理 5.1 的方法需要另外處理）。

### Catalan 數：二元樹

令 `C_n` 是有 n 個內部節點的二元樹數量（每個節點有可能為空的左、右子樹）。一棵非空樹 = 根 + 左子樹（i 個節點）+ 右子樹（n − 1 − i 個節點）：

```text
C_0 = 1，  C_n = Σ_{i=0}^{n−1} C_i C_{n−1−i}   (n ≥ 1)
```

右邊是卷積，正好是乘法。設 `C(x) = Σ C_n x^n`，「根」貢獻一個 x，兩棵子樹貢獻 `C(x)^2`，加上空樹的 1：

```text
C(x) = 1 + x C(x)^2
```

這是 C(x) 的二次方程式。解得 `C(x) = (1 ± √(1 − 4x)) / (2x)`。取負號，因為 `C(0)` 必須有限（取正號的話分子在 x = 0 時是 2，除以 2x 會爆掉）。

接著用廣義二項式定理展開 `√(1 − 4x) = Σ_k C(1/2, k)(−4x)^k`，其中 `C(1/2, k) = (1/2)(1/2 − 1)...(1/2 − k + 1) / k!`。可以驗證 k ≥ 1 時

```text
−C(1/2, k) (−4)^k = (2/k) C(2k − 2, k − 1)
```

所以 `1 − √(1 − 4x)` 的 `x^(n+1)` 係數是 `(2/(n+1)) C(2n, n)`，除以 2x 之後：

```text
C_n = C(2n, n) / (n + 1)
```

那條廣義二項式恆等式先用精確分數驗證，再拿三種方法對 Catalan 數：

```python
"""檢查 sqrt(1-4x) 展開係數的恆等式：−C(1/2, k)(−4)^k = (2/k)·C(2k−2, k−1)，k >= 1。"""
from fractions import Fraction
from math import comb


def gen_binom(r, k):
    """廣義二項式係數 C(r, k) = r(r−1)…(r−k+1)/k!，r 可以是分數。"""
    out = Fraction(1)
    for i in range(k):
        out = out * (r - i) / (i + 1)
    return out


half = Fraction(1, 2)
for k in range(1, 9):
    lhs = -gen_binom(half, k) * (-4) ** k
    rhs = Fraction(2, k) * comb(2 * k - 2, k - 1)
    print(f"k={k}: {str(lhs):>5} {str(rhs):>5}  C_(k-1) = {lhs / 2}")
    assert lhs == rhs
```

輸出：

```text
k=1:     2     2  C_(k-1) = 1
k=2:     2     2  C_(k-1) = 1
k=3:     4     4  C_(k-1) = 2
k=4:    10    10  C_(k-1) = 5
k=5:    28    28  C_(k-1) = 14
k=6:    84    84  C_(k-1) = 42
k=7:   264   264  C_(k-1) = 132
k=8:   858   858  C_(k-1) = 429
```

```python
"""Catalan：括號序列暴力、遞迴卷積、封閉公式。"""
from itertools import product
from math import comb


def balanced(seq):
    depth = 0
    for c in seq:
        depth += 1 if c == "(" else -1
        if depth < 0:
            return False
    return depth == 0


C = [1]
for n in range(1, 12):
    C.append(sum(C[i] * C[n - 1 - i] for i in range(n)))
for n in range(0, 12):
    brute = sum(1 for s in product("()", repeat=2 * n) if balanced(s)) if n <= 9 else None
    closed = comb(2 * n, n) // (n + 1)
    print(f"n={n:>2}: 暴力 {str(brute):>5}  卷積 {C[n]:>5}  C(2n,n)/(n+1) {closed:>5}")
```

輸出：

```text
n= 0: 暴力     1  卷積     1  C(2n,n)/(n+1)     1
n= 1: 暴力     1  卷積     1  C(2n,n)/(n+1)     1
n= 2: 暴力     2  卷積     2  C(2n,n)/(n+1)     2
n= 3: 暴力     5  卷積     5  C(2n,n)/(n+1)     5
n= 4: 暴力    14  卷積    14  C(2n,n)/(n+1)    14
n= 5: 暴力    42  卷積    42  C(2n,n)/(n+1)    42
n= 6: 暴力   132  卷積   132  C(2n,n)/(n+1)   132
n= 7: 暴力   429  卷積   429  C(2n,n)/(n+1)   429
n= 8: 暴力  1430  卷積  1430  C(2n,n)/(n+1)  1430
n= 9: 暴力  4862  卷積  4862  C(2n,n)/(n+1)  4862
n=10: 暴力  None  卷積 16796  C(2n,n)/(n+1) 16796
n=11: 暴力  None  卷積 58786  C(2n,n)/(n+1) 58786
```

暴力數的是 n 對括號的合法序列，不是二元樹。兩者數量相同是因為有一個雙射：二元樹 T 對應到 `"(" + 左子樹的序列 + ")" + 右子樹的序列`，空樹對應空字串。每個非空合法序列都可以唯一地切成 `( A ) B`——找到和第一個左括號配對的右括號即可。所以兩個結構滿足同一個遞迴、同一個初始值。暴力只跑到 n = 9（`2^18 ≈ 26` 萬個字串），n = 10 以後交給公式。

Catalan 數在 CS 到處出現：n + 1 個矩陣連乘的括號方式、n + 2 邊凸多邊形的三角剖分、合法的 push/pop 序列、AST 的形狀數。看到「一個東西切成兩半、兩半各自遞迴」就該想到它。

### 整數分割：無窮乘積

把正整數 n 寫成正整數之和、不計順序，方法數記 `p(n)`。例如 `4 = 4 = 3+1 = 2+2 = 2+1+1 = 1+1+1+1`，`p(4) = 5`。這是 Ch 3 「把相同的球放進相同的盒子」那一格。

一個分割由「用了幾個 1、幾個 2、幾個 3……」決定，各部分獨立：

```text
Σ p(n) x^n = (1 + x + x^2 + ...)(1 + x^2 + x^4 + ...)(1 + x^3 + x^6 + ...) ...
           = Π_{k≥1} 1 / (1 − x^k)
```

第 k 個因子的 `x^(jk)` 項代表「用了 j 個 k」。展開時從每個因子各挑一項，指數相加就是 n。

同樣的手法可以證明 Euler 的定理：**把 n 分成相異部分的方法數，等於分成奇數部分的方法數**。

**證明**：相異部分的生成函數是 `Π (1 + x^k)`（每個 k 用 0 或 1 次）。利用 `1 + x^k = (1 − x^(2k)) / (1 − x^k)`：

```text
Π_{k≥1} (1 + x^k) = Π_{k≥1} (1 − x^(2k)) / Π_{k≥1} (1 − x^k)
```

分子的因子 `(1 − x^2)(1 − x^4)...` 恰好消掉分母中所有偶數 k 的因子，剩下 `Π_{k 奇} 1/(1 − x^k)`，這正是只用奇數部分的生成函數。兩個生成函數相等，係數全部相等。證畢。

```python
"""整數分割：生成函數的係數 vs 遞迴列舉；相異部分 = 奇數部分。"""
N = 30


def gf_product(n, allowed, distinct):
    """展開 Π 的係數到 x^n：distinct=True 時每個 k 用 0/1 次，否則任意次。"""
    coef = [1] + [0] * n
    for k in allowed:
        if distinct:
            for i in range(n, k - 1, -1):      # 倒著做，避免同一個 k 用兩次
                coef[i] += coef[i - k]
        else:
            for i in range(k, n + 1):          # 正著做 = 乘上 1/(1 - x^k)
                coef[i] += coef[i - k]
    return coef


def brute(n, max_part=None):
    """直接列舉：最大部分不超過 max_part 的分割數。"""
    if max_part is None:
        max_part = n
    if n == 0:
        return 1
    return sum(brute(n - k, k) for k in range(1, min(n, max_part) + 1))


p = gf_product(N, range(1, N + 1), False)
print("p(0..15) =", p[:16])
assert all(p[n] == brute(n) for n in range(N + 1))
print(f"n <= {N} 生成函數與遞迴列舉一致；p({N}) = {p[N]}")
d = gf_product(N, range(1, N + 1), True)
o = gf_product(N, range(1, N + 1, 2), False)
print("相異部分 :", d[:16])
print("奇數部分 :", o[:16])
print("n <= 30 全部相等:", d == o)
```

輸出：

```text
p(0..15) = [1, 1, 2, 3, 5, 7, 11, 15, 22, 30, 42, 56, 77, 101, 135, 176]
n <= 30 生成函數與遞迴列舉一致；p(30) = 5604
相異部分 : [1, 1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 22, 27]
奇數部分 : [1, 1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 22, 27]
n <= 30 全部相等: True
```

`gf_product` 裡正著跑和倒著跑的差別，就是 0/1 背包和完全背包的差別——你在 DP 裡寫過的那兩個迴圈，其實是在乘兩種不同的冪級數。生成函數給了 DP 一個代數解釋。

## 指數生成函數（EGF）：標號結構

OGF 適合**無標號**的結構（括號序列、分割）。碰到**標號**結構——頂點有名字的圖、排列——拼接時還要決定「哪些標號分給哪一塊」，OGF 的卷積少算了這個選擇。

**定義**：`A(x) = Σ_{n≥0} a_n x^n / n!`。

**標號乘積**：把 `{1..n}` 分成兩塊，k 個標號給 A 結構、n − k 個給 B 結構，方法數是

```text
Σ_k C(n, k) a_k b_{n−k}
```

而 EGF 的乘積 `A(x) B(x)` 在 `x^n / n!` 的係數是 `n! Σ_k (a_k / k!)(b_{n−k} / (n−k)!) = Σ_k C(n, k) a_k b_{n−k}`。`1/n!` 的正規化讓 `C(n, k)` 自動出現，這就是 EGF 為什麼適合標號結構。

### 指數公式：圖 = 連通元件的集合

**定理 5.2（指數公式）**：令 `G(x)` 是所有標號圖的 EGF，`C(x)` 是連通標號圖的 EGF（不含空圖，`c_0 = 0`）。則 `G(x) = exp(C(x))`。

**證明**：`C(x)^k` 的 `x^n/n!` 係數，依標號乘積的推廣，數的是「把 `{1..n}` 分成 k 個**有序**、非空的塊，每塊上放一個連通圖」的方法數。一張恰有 k 個連通元件的圖，對應到這樣的有序 k 元組恰好 `k!` 次（k 個元件的標號集合互不相交且非空，所以 k! 種排列順序給出 k! 個不同的有序 k 元組）。因此恰有 k 個元件的圖的 EGF 是 `C(x)^k / k!`。每張圖恰有一個元件數 k ≥ 0（空圖 k = 0），對 k 求和：

```text
G(x) = Σ_{k≥0} C(x)^k / k! = exp(C(x))
```

證畢。

所以 `C(x) = log G(x)`。而 `G(x)` 我們早就知道：n 點標號圖有 `2^C(n,2)` 張（Ch 2 的雙射），`G(x) = Σ 2^C(n,2) x^n / n!`。這個級數對任何 x ≠ 0 都發散（`2^(n²/2)` 長得比 n! 快太多），但在形式冪級數的世界裡，log 照樣定義得好好的。

**怎麼計算 log**：令 `B = log A`（要求 `a_0 = 1`），則 `B' = A' / A`，也就是 `A · B' = A'`。比較 `x^(n−1)` 的係數：

```text
Σ_{k=1}^{n} k b_k a_{n−k} = n a_n   ⇒   n b_n = n a_n − Σ_{k=1}^{n−1} k b_k a_{n−k}
```

從 `b_1` 開始依序解出每個係數。

```python
"""EGF：連通標號圖 = log(所有標號圖)，對照 itertools 暴力 + NetworkX 判連通。"""
from fractions import Fraction
from itertools import combinations
from math import comb, factorial
import networkx as nx

N = 10


def log_series(a, N):
    """a[0] == 1，回傳 log(a) 的前 N+1 項：n·b_n = n·a_n − Σ_{k=1}^{n-1} k·b_k·a_{n−k}。"""
    b = [Fraction(0)] * (N + 1)
    for n in range(1, N + 1):
        s = n * a[n] - sum(k * b[k] * a[n - k] for k in range(1, n))
        b[n] = s / n
    return b


g = [Fraction(2 ** comb(n, 2), factorial(n)) for n in range(N + 1)]
c = log_series(g, N)
connected = [int(c[n] * factorial(n)) for n in range(N + 1)]
print("連通標號圖 (EGF 取 log):", connected[1:])


def brute_connected(n):
    pairs = list(combinations(range(n), 2))
    total = 0
    for mask in range(1 << len(pairs)):
        G = nx.Graph()
        G.add_nodes_from(range(n))
        G.add_edges_from(p for i, p in enumerate(pairs) if mask >> i & 1)
        total += nx.is_connected(G)
    return total


print("暴力 n=1..6           :", [brute_connected(n) for n in range(1, 7)])
```

輸出：

```text
連通標號圖 (EGF 取 log): [1, 1, 4, 38, 728, 26704, 1866256, 251548592, 66296291072, 34496488594816]
暴力 n=1..6           : [1, 1, 4, 38, 728, 26704]
```

前 6 項和暴力完全一致，這是 OEIS 的 A001187。n = 10 時有約 `3.4 × 10^13` 張連通標號圖，而所有標號圖是 `2^45 ≈ 3.5 × 10^13` 張——幾乎所有圖都連通。這個「幾乎」會在 [Ch 33](./33-random-graphs.md) 變成精確的機率陳述。

`Fraction` 在這裡是必要的：係數是 `2^C(n,2) / n!` 這種分數，float 在 n = 10 已經會丟失精度，最後乘回 `n!` 時就不是整數了。

### 有根標號樹：T = x · e^T

一棵**有根標號樹**等於「一個根」加上「一個由有根子樹構成的集合」（根的每個孩子是一棵子樹的根）。用 EGF 的語言，根貢獻 x，「集合」對應 exp：

```text
T(x) = x · exp(T(x))
```

這個方程式不能用代數直接解，但可以**迭代**：從 `T = 0` 開始反覆代入右邊。每次迭代至少多確定一個係數，因為右邊乘了 x。

```python
"""有根標號樹：T(x) = x·exp(T(x))，迭代求係數，對照 n^(n-1)。"""
from fractions import Fraction
from math import factorial

N = 9


def mul(a, b):
    return [sum(a[i] * b[n - i] for i in range(n + 1)) for n in range(N + 1)]


def exp_series(a):
    """a[0] == 0：由 e' = a'·e 得 n·e_n = Σ_{k=1}^{n} k·a_k·e_{n−k}。"""
    e = [Fraction(0)] * (N + 1)
    e[0] = Fraction(1)
    for n in range(1, N + 1):
        e[n] = sum(k * a[k] * e[n - k] for k in range(1, n + 1)) / n
    return e


T = [Fraction(0)] * (N + 1)
for _ in range(N):
    T = [Fraction(0)] + exp_series(T)[:N]      # 乘上 x = 係數整體右移一格
rooted = [int(T[n] * factorial(n)) for n in range(1, N + 1)]
print("T(x)=x·e^T 的 n!·[x^n]:", rooted)
print("n^(n-1)              :", [n ** (n - 1) for n in range(1, N + 1)])
```

輸出：

```text
T(x)=x·e^T 的 n!·[x^n]: [1, 2, 9, 64, 625, 7776, 117649, 2097152, 43046721]
n^(n-1)              : [1, 2, 9, 64, 625, 7776, 117649, 2097152, 43046721]
```

n 點有根標號樹有 `n^(n−1)` 棵；去掉「選根」的 n 倍，無根標號樹有 `n^(n−2)` 棵——Ch 2 窮舉樹時看到的 `1, 1, 3, 16, 125, 1296`。從 `T = x e^T` 精確推出 `n^(n−1)` 需要 **Lagrange 反演公式**，這裡**只給結論、不證**；[Ch 9](./09-trees-cayley-prufer.md) 會用 Prüfer 編碼給一個完全組合式的雙射證明。

## 從生成函數讀出成長速度

封閉形式的第二個用途是漸近分析。這裡**只給直覺**，嚴格的理論（singularity analysis）見延伸閱讀的 Flajolet–Sedgewick。

一個 OGF 若在 `|x| < R` 收斂、在 `|x| = R` 上有奇點，係數大致像 `R^(−n)` 乘上一個比指數慢的修正項。直覺上：幾何級數 `1/(1 − x/R) = Σ (1/R)^n x^n` 的奇點在 R，係數正好是 `R^(−n)`。

- Fibonacci：`(1 + x)/(1 − x − x^2)` 最近的奇點是 `1/φ ≈ 0.618`，所以 `a_n` 像 `φ^n`。
- Catalan：`√(1 − 4x)` 在 `x = 1/4` 有平方根型奇點，所以 `C_n` 像 `4^n`，平方根奇點貢獻修正項 `n^(−3/2)`：`C_n ~ 4^n / (n^(3/2) √π)`。

```python
"""主導奇點決定成長率：Fibonacci 比值 → φ，Catalan 與 4^n/(n^1.5·√π) 的比值 → 1。"""
from math import comb, sqrt, pi

a, b = 1, 1
for n in range(2, 41):
    a, b = b, a + b
    if n in (5, 10, 20, 40):
        print(f"F({n+1})/F({n}) = {b / a:.10f}")
print(f"φ           = {(1 + sqrt(5)) / 2:.10f}")
for n in (10, 100, 1000):
    cn = comb(2 * n, n) // (n + 1)
    ratio = (cn / 4 ** n) * n ** 1.5 * sqrt(pi)   # int / int 先縮到 float 範圍內
    print(f"n={n:>4}: C_n / (4^n / (n^1.5·√π)) = {ratio:.6f}")
```

輸出：

```text
F(6)/F(5) = 1.6000000000
F(11)/F(10) = 1.6181818182
F(21)/F(20) = 1.6180339985
F(41)/F(40) = 1.6180339887
φ           = 1.6180339887
n=  10: C_n / (4^n / (n^1.5·√π)) = 0.897803
n= 100: C_n / (4^n / (n^1.5·√π)) = 0.988862
n=1000: C_n / (4^n / (n^1.5·√π)) = 0.998876
```

比值趨近 1，但收斂得慢（n = 1000 時還差 0.1%）。`~` 的意思是**比值**趨近 1，不是差趨近 0。`cn / 4 ** n` 用 Python 的 int/int 真除法，先把兩個大整數的比算成 float，避免 `4 ** 1000` 直接轉 float 溢位。

## 對比與取捨

| 工具 | 適合的結構 | 拼接對應 | 例子 |
| --- | --- | --- | --- |
| 直接遞迴 / DP | 任何有遞迴結構的問題 | 程式碼 | 所有本章例子都能填表 |
| 特徵方程式 | 常係數線性遞迴 | — | Fibonacci、獨立集 |
| OGF | 無標號、按大小拼接 | 乘法 = 有序拼接 | Catalan、整數分割 |
| EGF | 標號結構 | 乘法 = 標號分配 + 拼接；exp = 集合 | 連通圖、樹、錯排 |
| 漸近分析 | 想知道成長速度 | 奇點位置 | `φ^n`、`4^n / n^1.5` |

為什麼不全部用 DP 就好？DP 回答「n = 50 是多少」，生成函數回答「為什麼」和「多快」。而且像連通標號圖這種問題，直接寫 DP 遞迴並不明顯（需要「固定頂點 1 所在的元件」這類技巧），但 `C = log G` 一行就寫完了。反過來，生成函數推不出封閉形式時，它至少給你一個正確的 DP。

## 踩雷集錦

1. **把形式冪級數當成函數去擔心收斂**：`G(x) = Σ 2^C(n,2) x^n/n!` 在 x ≠ 0 都發散，很多人因此覺得 `log G` 沒有意義。糾正：在形式冪級數環裡，log 由係數的遞迴定義，只要 `a_0 = 1` 就合法，完全不需要收斂。收斂只在做漸近分析時才重要。
2. **標號問題用了 OGF**：用 OGF 寫「圖 = 元件的集合」，會漏掉「標號怎麼分給各元件」的 `C(n, k)` 因子，算出來的數字全錯。糾正：先問「物件有沒有名字」。有名字用 EGF，沒名字用 OGF；無名字但有對稱性要等 [Ch 6](./06-burnside-polya.md)。
3. **下標差一位**：`a(n) = F(n + 2)` 還是 `F(n + 1)`？Catalan 的 n 是內部節點數還是葉子數？糾正：寫完遞迴後，用暴力算出前 5 項，和公式逐項對照；本章每個例子都這樣做。
4. **用浮點數算封閉形式**：Binet 公式在 n = 69 就錯；EGF 係數用 float 在 n = 10 左右就失真。糾正：封閉形式拿來分析；精確值用整數遞迴或 `Fraction`。
5. **二次方程取錯根**：Catalan 的 `C(x) = (1 ± √(1 − 4x))/(2x)` 取正號，會得到一個在 x = 0 爆掉的「解」。糾正：用初始條件（`C(0) = C_0 = 1` 有限）選根，並且展開前幾項檢查係數是否為非負整數。

## 進階：再往深一層

**符號方法（symbolic method）**：Flajolet 與 Sedgewick 把本章的手法系統化成一套「結構 → 方程式」的翻譯表：不交聯集 → 加法，笛卡兒積 → 乘法，序列 → `1/(1 − A)`，集合 → exp（標號）或 Pólya 指數（無標號），循環 → log。學會這張表，大部分組合計數問題都能直接寫出生成函數。

**無標號圖的生成函數**：連通**無標號**圖不能用 `log G` 求，因為無標號的「集合」不是 exp 而是 Pólya exponential `exp(Σ_k A(x^k)/k)`（多重集合的公式），而且 G 本身的係數（Ch 0 的 `1, 1, 2, 4, 11, 34, ...`）就需要 Ch 6 的 Burnside 引理才算得出來。

**和演算法分析的連結**：快排的平均比較次數、hash 表的期望探測次數、隨機二元搜尋樹的高度，經典的推導都是生成函數。Knuth 的《The Art of Computer Programming》大量使用這套方法，Sedgewick 與 Flajolet 的《An Introduction to the Analysis of Algorithms》是專門的教科書。

## 動手練習

1. 數長度 n、不含連續三個 1 的 0/1 字串。寫出遞迴、OGF，用暴力驗證到 n = 15，並用 OGF 的分母找出成長率（提示：`1 − x − x^2 − x^3` 的最小正根）。
2. 用 `T = x e^T` 的程式改出「錯排的 EGF 是 `e^(−x)/(1 − x)`」的驗證：用 `exp_series` 算 `e^(−x)`，乘上 `1/(1 − x)` 的係數，乘回 `n!`，和 Ch 4 的 `D(n)` 對照。為什麼這個 EGF 成立？（提示：排列 = 不動點的集合 × 錯排。）
3. 用 `log_series` 算「每個元件都是樹的標號圖」（森林）的數量：先求無根標號樹的 EGF（係數 `n^(n−2)`），取 exp，對 n ≤ 6 和暴力比對。
4. 證明 n 對括號的合法序列數也等於 n + 2 邊凸多邊形的三角剖分數。用程式列舉 n ≤ 6 的三角剖分驗證。

## 本章重點整理

- 線性遞迴用特徵方程式或 OGF 部分分式求解；封閉形式拿來分析，精確值用整數計算。
- OGF 的乘法是有序拼接；Catalan `C = 1 + x C^2`、分割 `Π 1/(1 − x^k)` 都是直接從結構翻譯。
- EGF 的乘法會自動帶出標號分配的 `C(n, k)`；「集合」對應 exp，所以連通圖 `C = log G`。
- 形式冪級數不需要收斂；`Fraction` 讓係數運算保持精確。
- 主導奇點的位置決定係數的指數成長率。

## 自我檢核

- [ ] 不看書，從「二元樹 = 根 + 左 + 右」推導出 `C(x) = 1 + x C(x)^2`，並說明為什麼要取負號的根。
- [ ] 解釋為什麼標號結構要用 EGF，`1/n!` 的正規化在乘法裡發揮了什麼作用。
- [ ] 向同事解釋：為什麼 `G(x) = Σ 2^C(n,2) x^n/n!` 處處發散，`log G(x)` 卻能正確算出連通圖數量。
- [ ] 想一個你寫過的 DP（例如背包、爬樓梯），寫出它對應的生成函數，並說明 DP 的迴圈在做哪一種冪級數運算。

## 延伸閱讀

### 書籍

- **[generatingfunctionology](https://www2.math.upenn.edu/~wilf/DownldGF.html)** — Herbert S. Wilf（第 3 版 A K Peters 2006；作者網站提供第 2 版免費 PDF）
  - **這本書的定位**：生成函數的經典入門書，口語化、例子多。
  - **讀哪幾章**：Chapter 1（Introductory ideas and examples）的遞迴解法，與 Chapter 3（Cards, decks, and hands: the exponential formula）——那正是本章定理 5.2 的完整推廣。
  - **前提知識**：本章內容與基礎微積分（冪級數的形式操作）。
- **[Analytic Combinatorics](https://ac.cs.princeton.edu/home/)** — Philippe Flajolet、Robert Sedgewick（Cambridge University Press，2009；作者提供免費 PDF）
  - **這本書的定位**：符號方法與奇點分析的權威著作。
  - **讀哪幾章**：Part A 的 Chapter I（Combinatorial Structures and OGFs）與 Chapter II（Labelled Structures and EGFs）就是本章的系統化版本。Part B 的奇點分析是本章「成長速度」一節的嚴格版，需要複變函數。
  - **前提知識**：Chapter I–II 有本章內容就能讀；Part B 需要複分析。
- **《Concrete Mathematics》** — Graham、Knuth、Patashnik（Addison-Wesley，第 2 版，1994）
  - **讀哪幾章**：Chapter 7（Generating Functions）。7.3 節用生成函數解遞迴，7.5 節是卷積（含 Catalan 數），7.6 節是 EGF。
  - **前提知識**：Ch 3 與本章。年代久但內容不會過時。

### 資料庫

- **[OEIS A001187：n 點連通標號圖數](https://oeis.org/A001187)**
  - **讀哪裡**：FORMULA 段列出了 `log` 關係與一個不需要生成函數的遞迴式；可以拿來當練習的另一條驗證路徑。
  - **能學到什麼**：同一個數列從 EGF、遞迴、漸近三個角度的描述。

生成函數處理了「有標號」和「沒有標號但沒有對稱」的計數。還剩最後一種，也是 Ch 0 留下的問題：n 點**無標號**圖有幾張？兩張圖只差頂點重新命名就算同一張，這需要把對稱性本身拿來計數。

→ [Ch 6 對稱計數：Burnside 與 Pólya](./06-burnside-polya.md)
