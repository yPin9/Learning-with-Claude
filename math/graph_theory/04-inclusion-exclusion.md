# Ch 4 — 排容原理與錯排

> **目標**：理解排容原理（inclusion–exclusion）為什麼對、怎麼把「沒有任何壞性質」的計數問題寫成排容公式，並用它算錯排、滿射、Euler φ、無孤立點的圖與色多項式，每個公式都和暴力計數對上。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。

## 為什麼需要這個？

[Ch 3](./03-counting-basics.md) 的工具擅長數「滿足某條件」的東西：選 k 個、排 k 個。但很多真實問題長這樣：

- 一個 hash 函數把 k 個 key 丟進 n 個 bucket，**每個** bucket 都至少拿到一個的機率是多少？
- n 個人抽籤交換禮物，**沒有人**抽到自己的機率是多少？
- 用 q 種顏色幫圖著色，**沒有任何**一條邊兩端同色的方法有幾種？（這就是 register allocation 問的問題，Ch 17。）
- 1 到 n 之中，和 n **沒有**共同質因數的數有幾個？（RSA 的 φ(n)。）

這些都是「避開一堆壞事件」的計數。直接數很難，因為壞事件會重疊：一個排列可能同時讓 1 和 3 都在原位。如果你把「1 在原位」和「3 在原位」的數量相減，同時兩者都在原位的排列就被扣了兩次。

排容原理是處理重疊的標準工具：先扣掉、發現扣太多就加回來、發現加太多再扣掉。這一章要讓你知道這個「來回修正」為什麼最後會剛好正確。

## 先建立直覺

兩個集合時，每個人都見過：

```text
     ┌───────────┐
     │   A   ┌───┼───────┐
     │       │A∩B│   B   │
     └───────┼───┘       │
             └───────────┘

|A ∪ B| = |A| + |B| − |A ∩ B|
```

`|A| + |B|` 把交集數了兩次，扣掉一次就對了。三個集合時：

```text
|A ∪ B ∪ C| = |A| + |B| + |C|
            − |A∩B| − |A∩C| − |B∩C|
            + |A∩B∩C|
```

為什麼最後要加回 `|A∩B∩C|`？看一個同時在三個集合裡的元素 x：第一行把它數了 3 次，第二行扣了 3 次，淨值 0——它被漏掉了，所以第三行要加回 1 次。

一般化的直覺：**看單一元素被數了幾次**。下面的證明就是把這個直覺寫精確。

## 排容原理

**定理 4.1（排容原理）**：對有限集合 `A_1, ..., A_m`，

```text
|A_1 ∪ ... ∪ A_m| = Σ_{∅ ≠ S ⊆ {1..m}} (−1)^(|S|+1) |A_S|，  其中 A_S = ∩_{i∈S} A_i
```

**證明**：看任一元素 x。若 x 不在任何 `A_i` 裡，左右兩邊都不數它。否則設 x 恰好屬於 t ≥ 1 個集合。x 屬於 `A_S` 若且唯若 S 是這 t 個集合的指標子集。所以右邊數 x 的次數是

```text
Σ_{j=1}^{t} (−1)^(j+1) C(t, j) = 1 − Σ_{j=0}^{t} (−1)^j C(t, j) = 1 − (1 − 1)^t = 1
```

第二個等號用了二項式定理（Ch 3），且 t ≥ 1 時 `(1 − 1)^t = 0`。每個在聯集裡的元素恰被數一次，等式成立。證畢。

實務上更常用的是**補集形式**。設全集 U，「壞性質」i 對應集合 `A_i`，想數**一個壞性質都沒有**的元素：

```text
N(沒有任何壞性質) = Σ_{S ⊆ {1..m}} (−1)^|S| N(S)，  N(S) = 至少擁有 S 中所有壞性質的元素數
```

`S = ∅` 那一項是 `N(∅) = |U|`。這個形式的關鍵在 `N(S)` 的定義：**至少**擁有 S 裡的性質（可能還有其他），而不是**恰好**擁有。`N(S)` 通常很好算，因為「強迫某些壞事發生」之後，剩下的部分常常是自由的。

套用排容的固定步驟：

1. 定義全集 U 與每個壞性質。
2. 算 `N(S)`，最好只依賴 `|S|`（對稱的情況）。
3. 若 `N(S)` 只依賴 `|S| = k`，把和式改寫成 `Σ_k (−1)^k C(m, k) N_k`。

## 例一：錯排

**錯排（derangement）**：沒有任何元素在原位的排列，也就是 `p(i) ≠ i` 對所有 i。令 `D(n)` 是 n 元錯排的數量。

**排容推導**：U 是 n 元所有排列，壞性質 i 是「`p(i) = i`」。若強迫集合 S 中的 k 個元素都在原位，其餘 n − k 個任意排列，`N(S) = (n − k)!`，只依賴 k。所以

```text
D(n) = Σ_{k=0}^{n} (−1)^k C(n, k) (n − k)! = n! Σ_{k=0}^{n} (−1)^k / k!
```

**遞迴**：`D(n) = (n − 1)(D(n − 1) + D(n − 2))`，`D(0) = 1`、`D(1) = 0`。

**組合證明**：看一個 n 元錯排 p 中，元素 n 被送到哪裡。`p(n) = i ≠ n`，i 有 n − 1 種選擇。固定 i 之後分兩種情況：

- `p(i) = n`（i 和 n 互換）：剩下 n − 2 個元素必須自成錯排，`D(n − 2)` 種。
- `p(i) ≠ n`：此時指向 n 的是另一個元素 j（`p(j) = n`，`j ≠ i`）。定義 `{1..n−1}` 上的 p'：`p'(j) = i`，其他元素 `p'(x) = p(x)`。也就是把「指向 n 的那根箭頭」改指向 i（i 已經空出來，因為原本指向 i 的是 n，而 n 被拿掉了）。p' 是 `{1..n−1}` 的排列；它沒有不動點，因為 `p'(j) = i ≠ j`，其他點沿用 p 的值。反過來，給任一個 `{1..n−1}` 上的錯排 p'，令 j 是 `p'` 下指向 i 的那個元素，設 `p(n) = i`、`p(j) = n`、其餘照抄，就得到一個 `p(n) = i` 且 `p(i) = p'(i) ≠ n` 的 n 元錯排。兩個方向互為反函數，所以這類 p 有 `D(n − 1)` 種。

兩種情況相加再乘上 i 的 n − 1 種選擇，得到遞迴。

**和 1/e 的關係**：`Σ (−1)^k / k!` 是 `e^(−1)` 的泰勒級數截到第 n 項。交錯級數且項的絕對值遞減，所以截斷誤差小於下一項的絕對值：

```text
|n!/e − D(n)| = n! · |Σ_{k>n} (−1)^k / k!| < n! / (n+1)! = 1/(n+1)
```

n ≥ 1 時誤差小於 1/2，所以 `D(n) = round(n!/e)`。直覺說法：n 夠大時，隨機排列是錯排的機率趨近 `1/e ≈ 0.3679`，而且收斂得非常快。

```python
"""錯排：排容公式、遞迴、n!/e 取整，對照 permutations 暴力。"""
from itertools import permutations
from math import comb, factorial, e


def d_pie(n):
    return sum((-1) ** k * comb(n, k) * factorial(n - k) for k in range(n + 1))


def d_rec(n):
    a, b = 1, 0          # D(0), D(1)
    if n == 0:
        return a
    for m in range(2, n + 1):
        a, b = b, (m - 1) * (a + b)
    return b


print(f"{'n':>2} {'暴力':>6} {'排容':>6} {'遞迴':>6} {'round(n!/e)':>11} {'D(n)/n!':>9}")
for n in range(0, 9):
    brute = sum(1 for p in permutations(range(n)) if all(p[i] != i for i in range(n)))
    r = round(factorial(n) / e)
    print(f"{n:>2} {brute:>6} {d_pie(n):>6} {d_rec(n):>6} {r:>11} {brute / factorial(n):>9.6f}")
print("1/e =", f"{1 / e:.6f}")
```

輸出：

```text
 n     暴力     排容     遞迴 round(n!/e)   D(n)/n!
 0      1      1      1           0  1.000000
 1      0      0      0           0  0.000000
 2      1      1      1           1  0.500000
 3      2      2      2           2  0.333333
 4      9      9      9           9  0.375000
 5     44     44     44          44  0.366667
 6    265    265    265         265  0.368056
 7   1854   1854   1854        1854  0.367857
 8  14833  14833  14833       14833  0.367882
1/e = 0.367879
```

注意 n = 0 那一列：暴力、排容、遞迴都說 `D(0) = 1`（空排列沒有任何元素在原位，空洞地滿足條件），但 `round(0!/e) = round(0.3679) = 0`。這和上面的誤差分析一致——那個 `D(n) = round(n!/e)` 的推導要求 n ≥ 1，因為 n = 0 時誤差上界 `1/(n+1) = 1` 不小於 1/2。公式的適用範圍不是裝飾。

`1, 0, 1, 2, 9, 44, 265, 1854, 14833` 是 OEIS 的 A000166。

## 例二：滿射與 Stirling 數

`[k] → [n]` 的滿射（每個 `j ∈ [n]` 都被打到）有幾個？壞性質 j 是「j 沒被打到」。強迫 S 中的 i 個值都沒被打到，每個輸入只能從剩下的 n − i 個值中選，`N(S) = (n − i)^k`：

```text
Surj(k, n) = Σ_{i=0}^{n} (−1)^i C(n, i) (n − i)^k
```

這就是「k 個 key 丟進 n 個 bucket，每個 bucket 都不空」的方法數。除以 `n!`（滿射的值域重新命名不算不同）得到第二類 Stirling 數 `S(k, n)`：把 k 個可區分的物件分成 n 個不可區分的非空群。它補上了 Ch 3 「twelvefold way」表裡「每盒至少 1」那一格。

```python
"""滿射數：排容 vs product 暴力。"""
from itertools import product
from math import comb


def surj(k, n):
    return sum((-1) ** j * comb(n, j) * (n - j) ** k for j in range(n + 1))


for k, n in [(3, 2), (4, 2), (4, 3), (5, 3), (6, 4), (3, 4)]:
    brute = sum(1 for f in product(range(n), repeat=k) if len(set(f)) == n)
    print(f"[{k}]->[{n}] 滿射: 暴力 {brute:>4}  排容 {surj(k, n):>4}")
```

輸出：

```text
[3]->[2] 滿射: 暴力    6  排容    6
[4]->[2] 滿射: 暴力   14  排容   14
[4]->[3] 滿射: 暴力   36  排容   36
[5]->[3] 滿射: 暴力  150  排容  150
[6]->[4] 滿射: 暴力 1560  排容 1560
[3]->[4] 滿射: 暴力    0  排容    0
```

最後一列 k < n：滿射不可能存在，排容公式的正負項剛好完全抵銷成 0。公式不需要特判，這是排容形式的一個好處。

## 例三：Euler φ 函數

`φ(n)` 是 1 到 n 之中和 n 互質的數的個數，RSA 的金鑰產生靠它（見 [密碼學 Ch 2 數論](../../security/cryptography/02-number-theory.md)）。設 n 的相異質因數是 `p_1, ..., p_r`，壞性質 i 是「被 `p_i` 整除」。被 S 中所有質數整除的數，就是被它們的乘積 d 整除的數，在 1 到 n 中有 `n / d` 個：

```text
φ(n) = Σ_{S ⊆ {p_1..p_r}} (−1)^|S| · n / Π_{p∈S} p = n · Π_i (1 − 1/p_i)
```

最後的乘積形式是把和式因式分解：展開 `Π (1 − 1/p_i)` 正好得到每個子集一項。

```python
"""Euler φ：用質因數做排容，對照 gcd 暴力。"""
from math import gcd
from itertools import combinations


def prime_factors(n):
    ps, d = [], 2
    while d * d <= n:
        if n % d == 0:
            ps.append(d)
            while n % d == 0:
                n //= d
        d += 1
    if n > 1:
        ps.append(n)
    return ps


def phi_pie(n):
    ps = prime_factors(n)
    total = 0
    for r in range(len(ps) + 1):
        for S in combinations(ps, r):
            prod = 1
            for p in S:
                prod *= p
            total += (-1) ** r * (n // prod)
    return total


bad = [n for n in range(1, 5001) if phi_pie(n) != sum(1 for k in range(1, n + 1) if gcd(k, n) == 1)]
print("n = 1..5000 不一致的個數:", len(bad))
for n in (12, 30, 97, 360):
    print(f"φ({n}) = {phi_pie(n)}，質因數 {prime_factors(n)}")
```

輸出：

```text
n = 1..5000 不一致的個數: 0
φ(12) = 4，質因數 [2, 3]
φ(30) = 8，質因數 [2, 3, 5]
φ(97) = 96，質因數 [97]
φ(360) = 96，質因數 [2, 3, 5]
```

`φ(360) = 360 × (1/2)(2/3)(4/5) = 96`。注意排容只看**相異**質因數：360 = `2^3 · 3^2 · 5`，指數不影響「被 2 整除」這個性質。

## 例四：沒有孤立點的圖

n 個標號頂點上，沒有孤立點（度數 0 的點）的簡單圖有幾張？壞性質 v 是「v 是孤立點」。強迫 k 個點孤立，等於刪掉它們，剩下 n − k 個點之間的邊任意，`N(S) = 2^C(n−k, 2)`：

```text
#（無孤立點的圖）= Σ_{k=0}^{n} (−1)^k C(n, k) 2^C(n−k, 2)
```

```python
"""沒有孤立點的標號圖數：排容 vs 暴力。"""
from itertools import combinations
from math import comb


def no_isolated_pie(n):
    return sum((-1) ** k * comb(n, k) * 2 ** comb(n - k, 2) for k in range(n + 1))


for n in range(0, 7):
    pairs = list(combinations(range(n), 2))
    brute = 0
    for mask in range(1 << len(pairs)):
        deg = [0] * n
        for i, (u, v) in enumerate(pairs):
            if mask >> i & 1:
                deg[u] += 1
                deg[v] += 1
        brute += all(d > 0 for d in deg)
    print(f"n={n}: 暴力 {brute:>5}  排容 {no_isolated_pie(n):>5}")
```

輸出：

```text
n=0: 暴力     1  排容     1
n=1: 暴力     0  排容     0
n=2: 暴力     1  排容     1
n=3: 暴力     4  排容     4
n=4: 暴力    41  排容    41
n=5: 暴力   768  排容   768
n=6: 暴力 27449  排容 27449
```

這是 OEIS 的 A006129。n = 6 時 32768 張圖裡有 27449 張沒有孤立點，約 84%；n 越大比例越接近 1，[Ch 33](./33-random-graphs.md) 會用機率的語言精確描述它的門檻。

一個自然的追問：**連通**的標號圖有幾張？「連通」不能寫成「避開一組獨立的壞性質」，排容不直接適用；它需要 Ch 5 的生成函數（指數生成函數的對數關係）。

## 例五：色多項式

這是本章和圖論最直接的連結。用 q 種顏色幫圖 G 的頂點著色，相鄰點不同色，方法數記為 `P(G, q)`。

**排容推導（Whitney）**：全集是所有 `q^n` 種著色，壞性質 e 是「邊 e 的兩端同色」。強迫邊子集 S 中每條邊兩端同色，等於在子圖 `(V, S)` 的每個連通元件內塗同一色，各元件獨立選色。若 `(V, S)` 有 `c(S)` 個連通元件，`N(S) = q^c(S)`：

```text
P(G, q) = Σ_{S ⊆ E} (−1)^|S| q^c(S)
```

右邊是 q 的多項式，所以 `P(G, q)` 叫**色多項式（chromatic polynomial）**。這裡 `N(S)` 不只依賴 `|S|`，而是依賴 S 的結構，所以要真的列舉邊子集。

```python
"""色多項式：對邊子集做排容（Whitney），對照暴力著色計數。"""
from itertools import product
import networkx as nx


def components(n, edges):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    c = n
    for u, v in edges:
        ru, rv = find(u), find(v)
        if ru != rv:
            parent[ru] = rv
            c -= 1
    return c


def chrom_pie(G, q):
    nodes = list(G)
    idx = {v: i for i, v in enumerate(nodes)}
    E = [(idx[u], idx[v]) for u, v in G.edges()]
    total = 0
    for mask in range(1 << len(E)):
        S = [E[i] for i in range(len(E)) if mask >> i & 1]
        total += (-1) ** len(S) * q ** components(len(nodes), S)
    return total


def chrom_brute(G, q):
    nodes = list(G)
    idx = {v: i for i, v in enumerate(nodes)}
    E = [(idx[u], idx[v]) for u, v in G.edges()]
    return sum(1 for col in product(range(q), repeat=len(nodes))
               if all(col[u] != col[v] for u, v in E))


tests = [("C5", nx.cycle_graph(5)), ("K4", nx.complete_graph(4)),
         ("Petersen", nx.petersen_graph())]
for name, G in tests:
    row = []
    for q in (2, 3, 4):
        a, b = chrom_pie(G, q), chrom_brute(G, q)
        assert a == b
        row.append(f"P({q})={a}")
    print(f"{name:>8}: " + "  ".join(row))
```

輸出：

```text
      C5: P(2)=0  P(3)=30  P(4)=240
      K4: P(2)=0  P(3)=0  P(4)=24
Petersen: P(2)=0  P(3)=120  P(4)=12960
```

讀法：

- **C5**（5-圈）：`P(2) = 0`，奇圈不能 2 著色（Ch 7 的二分圖刻畫）。閉式是 `P(C_n, q) = (q − 1)^n + (−1)^n (q − 1)`，代 n = 5、q = 3 得 `32 − 2 = 30`。
- **K4**：需要 4 色，`P(4) = 4! = 24`（四個點顏色全相異）。
- **Petersen**：3 色就夠，`P(3) = 120`。它有 15 條邊，排容要列舉 `2^15 = 32768` 個邊子集；暴力著色在 q = 4 時要試 `4^10 ≈ 105` 萬種，兩者都還在可接受範圍。

色多項式的係數本身有圖論意義（例如 `q^(n−1)` 的係數是 `−|E|`），[Ch 15](./15-graph-coloring.md) 會用刪縮遞迴（deletion–contraction）給出比排容快得多的算法。NetworkX 3.7 有 `nx.chromatic_polynomial`，但它依賴 SymPy，本課的釘版環境沒有安裝，呼叫會丟 `ModuleNotFoundError: No module named 'sympy'`。

## 截斷排容：Bonferroni 不等式

排容的和式有 `2^m` 項，m 大時算不完。截斷在前幾項會怎樣？

**Bonferroni 不等式**：在補集形式中，截到 `|S| ≤ r` 的部分和，r 為偶數時是真值的**上界**，r 為奇數時是**下界**。

```python
"""截斷排容：部分和交替地高估與低估（以錯排 n=8 為例）。"""
from itertools import permutations
from math import comb, factorial

n = 8
exact = sum(1 for p in permutations(range(n)) if all(p[i] != i for i in range(n)))
partial = 0
for k in range(n + 1):
    partial += (-1) ** k * comb(n, k) * factorial(n - k)
    tag = "高估" if partial > exact else ("低估" if partial < exact else "精確")
    print(f"截到 k={k}: {partial:>6}  {tag}")
print("真值:", exact)
```

輸出：

```text
截到 k=0:  40320  高估
截到 k=1:      0  低估
截到 k=2:  20160  高估
截到 k=3:  13440  低估
截到 k=4:  15120  高估
截到 k=5:  14784  低估
截到 k=6:  14840  高估
截到 k=7:  14832  低估
截到 k=8:  14833  精確
真值: 14833
```

截到 k = 1 時算出 0，是一個合法但毫無用處的下界。在錯排這個例子，後面的項很快變小，截到 k = 5 已經只差 49；但這不是一般保證——當 `N(S)` 隨 |S| 遞減得不夠快時，部分和可以在真值兩側大幅擺盪。實務上 Bonferroni 用在機率估計：`P(至少一個壞事件) ≤ Σ P(A_i)`（union bound，截到 r = 1 的形式），這是 [Ch 31](./31-probabilistic-method.md) 機率方法最常用的一行不等式。

## 對比與取捨

| 方法 | 何時好用 | 代價 | 例子 |
| --- | --- | --- | --- |
| 直接計數 | 條件是「滿足」而不是「避開」 | 遇到重疊就難 | Ch 3 的排列組合 |
| 補集 + 排容 | 壞性質多但 `N(S)` 好算 | `2^m` 項；若 `N(S)` 只依賴 S 的大小則降為 m + 1 項 | 錯排、滿射、φ |
| 排容 + 結構列舉 | `N(S)` 依賴 S 的結構 | 指數時間 | 色多項式 |
| 遞迴 | 能找到「拿掉一個元素」的分解 | 需要組合論證 | 錯排遞迴、刪縮 |
| 生成函數 | 結構可以組合（Ch 5） | 需要代數工具 | 連通圖計數 |

錯排同時有排容公式和遞迴，兩者互相驗證；色多項式也同時有排容和刪縮兩種算法。看到一個計數問題，盡量找兩種獨立的推導，再加上暴力，三方對照。

## 踩雷集錦

1. **`N(S)` 算成「恰好」而不是「至少」**：數錯排時把 `N(S)` 寫成「恰好 S 中的元素在原位、其他都不在」，那就變成在用錯排定義錯排，循環了。糾正：`N(S)` 的定義是「S 中的壞性質都發生，其他的**不管**」。這也是 `N(S)` 好算的原因。
2. **忘記 `S = ∅` 那一項**：寫成 `Σ_{k=1}^{n}`，結果差了一個 `|U|` 並且符號全反。糾正：補集形式從 k = 0 開始，`N(∅) = |U|`。
3. **以為壞性質之間要獨立**：排容不需要事件獨立，也不需要對稱。色多項式的壞性質（邊兩端同色）高度相關，排容依然精確。需要的只是能算 `N(S)`。對稱只是讓計算從 `2^m` 項降到 m + 1 項。
4. **把公式用到適用範圍外**：`D(n) = round(n!/e)` 在 n = 0 時給 0，真值是 1。排容和式本身對 n = 0 正確，取整近似不是。糾正：每個「簡化形式」都要回頭檢查推導時用到的條件。
5. **Euler φ 用了重複的質因數**：把 `360 = 2·2·2·3·3·5` 的每個因子都當成一個壞性質，會把「被 2 整除」扣三次。糾正：壞性質是「被 p 整除」，p 取**相異**質因數。程式裡 `prime_factors` 用 `while n % d == 0` 把同一個質數除乾淨，就是為了這個。

## 進階：再往深一層

**Möbius 反演**：排容是 Möbius 反演在「子集偏序」上的特例。若 `g(S) = Σ_{T ⊇ S} f(T)`（`g` 是「至少」、`f` 是「恰好」），則 `f(S) = Σ_{T ⊇ S} (−1)^(|T|−|S|) g(T)`。換成「整除偏序」就得到數論的 Möbius 函數 μ，換成一般偏序就是 Rota 在 1964 年統一的理論。Stanley《Enumerative Combinatorics》Vol. 1 第 3 章有完整處理。

**計算複雜度**：排容讓某些 `#P` 問題可以在 `O(2^n · poly(n))` 時間內精確計算，比樸素的 `n!` 快很多。經典例子是 Ryser 公式算 permanent，以及 Björklund、Husfeldt、Koivisto 在 2009 年用排容在 `O*(2^n)` 時間算圖的色數（`O*` 表示忽略多項式因子）。這是 exact exponential algorithms 領域的核心技巧之一。

**資安場景**：hash 表「每個 bucket 都不空」的機率就是 `Surj(k, n) / n^k`，這是 coupon collector 問題的精確版本；它也是估計「需要多少次隨機探測才能覆蓋整個位址空間」的工具。生日問題則是反過來：「k 個值全部不碰撞」，是 `P(n, k) / n^k`。

## 動手練習

1. 秘密聖誕老人：n 個人各抽一張寫了別人名字的籤，沒人抽到自己。寫程式模擬 10 萬次（seed = 2026）的隨機排列，統計錯排的比例，和 `D(n) / n!` 比較，n 取 3、5、10。
2. 用排容推導「n 元排列中**恰好** r 個元素在原位」的數量是 `C(n, r) D(n − r)`，並用暴力驗證到 n = 7。這和踩雷 1 的「至少 vs 恰好」有什麼關係？
3. 寫 `P(C_n, q)` 的閉式並證明它（提示：對 n 歸納，用刪縮 `P(G, q) = P(G − e, q) − P(G / e, q)`，或者直接相信這個遞迴、Ch 15 會證）。用 `chrom_brute` 驗證 n = 3..7、q = 2..4。
4. 修改 `no_isolated_pie`，數「最小度數 ≥ 1 且邊數恰為 m」的標號圖，對 n = 5 列出每個 m 的值並用暴力驗證。

## 本章重點整理

- 排容的證明核心：恰屬於 t ≥ 1 個集合的元素被數了 `1 − (1 − 1)^t = 1` 次。
- 補集形式 `Σ (−1)^|S| N(S)`，`N(S)` 是「至少」擁有 S 中所有壞性質的元素數。
- `N(S)` 只依賴 |S| 時，和式降為 m + 1 項：錯排、滿射、無孤立點的圖。
- 色多項式 `P(G, q) = Σ_{S⊆E} (−1)^|S| q^c(S)` 是排容在圖著色上的直接應用。
- 截斷排容給交替的上下界（Bonferroni），r = 1 就是 union bound。

## 自我檢核

- [ ] 不看書，寫出排容原理的證明，並說出二項式定理在哪一步用到。
- [ ] 解釋為什麼錯排的 `N(S) = (n − |S|)!`，以及如果誤把 `N(S)` 理解成「恰好」會發生什麼事。
- [ ] 用排容推導 `[k] → [n]` 的滿射數，並說明 k < n 時公式為什麼自動給出 0。
- [ ] 向一位做 register allocation 的同事解釋：「用 q 個暫存器分配的方法數」怎麼用排容表示，以及為什麼這個公式在實務上不能直接用。

## 延伸閱讀

### 書籍

- **《Enumerative Combinatorics, Volume 1》** — Richard P. Stanley（Cambridge University Press，第 2 版，2012）
  - **這本書的定位**：排容與 Möbius 反演的標準參考。
  - **讀哪幾章**：Chapter 2（Sieve Methods）的 2.1–2.3 節：排容原理、例子、permanent。Chapter 3 的 Möbius 反演是進階延伸，讀完 Part 2 後再看。
  - **前提知識**：本章內容；Chapter 3 需要偏序集的基本概念。
- **《Introductory Combinatorics》** — Richard A. Brualdi（Pearson，第 5 版，2010）
  - **這本書的定位**：大學部組合學教科書，例子多、步調比 Stanley 慢得多。
  - **讀哪幾章**：講排容原理的那一章（第 5 版是 Chapter 6，The Inclusion–Exclusion Principle and Applications）：錯排、有禁止位置的排列。不同印刷版的章號可能不同，以章名查找。
  - **前提知識**：Ch 3 的排列組合。
- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **讀哪幾章**：Section 5.3（Enumerative Aspects）的色多項式：刪縮遞迴與色多項式的基本性質，本章例五的圖論脈絡。
  - **前提知識**：本章例五；需要知道著色的定義（Ch 15 會正式介紹）。

### 論文

- **[Set Partitioning via Inclusion–Exclusion](https://doi.org/10.1137/070683933)** — Andreas Björklund、Thore Husfeldt、Mikko Koivisto，*SIAM Journal on Computing* 39(2): 546–563（2009；會議版本發表於 FOCS 2006）
  - **核心貢獻**：用排容在 `O*(2^n)` 時間內算出圖的色數與更一般的集合分割問題，是 exact exponential algorithm 的里程碑。
  - **讀哪裡**：Section 1（Introduction）與色數的那一節；了解「把『恰好覆蓋』轉成『至少/至多』再排容」的核心想法即可，複雜度分析可以跳過。
  - **和本章的關聯**：本章例五用排容算色多項式需要列舉 `2^|E|` 個邊子集；這篇論文改成對**頂點**子集排容，指數從邊數降到頂點數。
  - **前提知識**：本章內容與 Big-O；Ch 15 的著色定義。

### 資料庫

- **[OEIS A000166（錯排數）](https://oeis.org/A000166)** 與 **[OEIS A006129（無孤立點的標號圖）](https://oeis.org/A006129)**
  - **讀哪裡**：FORMULA 段。A000166 列出了本章的排容公式、遞迴與 `round(n!/e)`；A006129 列出本章例四的排容公式。
  - **能學到什麼**：同一個數列的多種公式，以及它們彼此之間的推導關係。

排容處理的是「避開壞事件」，但連通圖計數、Catalan 數這類問題，結構是「由小塊組合成大塊」。下一章用生成函數把這種組合結構變成代數。

→ [Ch 5 遞迴關係與生成函數](./05-recurrences-generating-functions.md)
