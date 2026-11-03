# Ch 31 — 機率方法

> **目標**：學會用「隨機挑一個，證明它好的機率大於 0」來證明組合物件存在——期望值論證、刪點法（alteration）、Lovász Local Lemma、二階矩法；並且親手跑一遍：把隨機切割去隨機化、算出 Erdős 的 Ramsey 下界、用 Moser–Tardos 演算法把 Local Lemma 變成可以執行的 SAT 求解器。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

前面每一章證明「存在」時，我們都把東西**構造**出來：增廣路、著色、生成樹。構造證明很踏實，但有一類問題，構造幾乎不可能：

> 存在一張 n 點的圖，既沒有 k 個點的團（clique），也沒有 k 個點的獨立集，其中 n 是 k 的指數函數。

1947 年 Erdős 用三行字證明了它：把 `K_n` 的每條邊隨機塗紅或藍，算出「出現單色 `K_k`」的期望個數小於 1，所以**一定存在**一種塗法完全沒有單色 `K_k`。他沒有、也不需要說出是哪一種塗法。

這個論證的力量在於它不需要理解解長什麼樣，只要理解**隨機的東西平均長什麼樣**。代價是：我們知道解存在，卻往往不知道怎麼找。Erdős 的 Ramsey 下界到今天仍然沒有一個能達到同樣指數成長的明確構造——近年雖有重要進展，明確構造的品質仍遠遠落後於隨機。Ch 30 的 expander 也是同一個故事。

在系統與資安工作裡，機率方法的痕跡隨處可見：隨機化演算法的分析、hash 碰撞的生日界、bloom filter 的誤判率、SAT 求解器的隨機重啟。本章把這些直覺背後的證明技巧系統化。

## 先建立直覺

機率方法只有兩條核心原理，都是你已經知道的事：

```text
原理 1（平均原理）：一群數的平均是 μ，那麼至少有一個 ≥ μ，至少有一個 ≤ μ。
                    → 若 E[X] ≥ t，則存在某個結果使 X ≥ t。

原理 2（不可能全壞）：若「壞」的機率 < 1，則「好」的機率 > 0。
                    → 若 Σ P(壞事件 i) < 1（union bound），則存在一個結果，所有壞事件都沒發生。
```

整章的技巧，都是在把一個存在性問題改寫成上面兩句話之一，然後計算期望值或機率。

**一個常見誤解**：機率方法證的是**確定性**的命題。「存在一張圖⋯⋯」是個真或假的陳述，裡面沒有任何機率；機率只是我們用來數數的工具——`P(好) > 0` 只是「好的物件個數 > 0」除以總數的另一種寫法。

## 期望值論證

**期望值的線性性**：`E[X + Y] = E[X] + E[Y]`，**不需要** X 與 Y 獨立。這是整個方法最常用、也最容易被低估的工具：把一個複雜的計數拆成很多個指示變數的和，每一個的期望值都容易算。

### 例一：每張圖都有一個至少切到一半邊的切割

**定理 31.1**：任何有 m 條邊的圖，存在一個切割 `(S, S̄)` 使 `|E(S, S̄)| ≥ m/2`。

**證明**：每個點獨立地以 1/2 機率放進 S。對每條邊 uv，令 `I_uv = 1` 若 u、v 分在兩側。`P(I_uv = 1) = 1/2`（u 怎麼放都行，v 有一半機率放到另一側）。切邊數 `X = Σ I_uv`，由線性性 `E[X] = m/2`。由平均原理，存在一個切割使 `X ≥ m/2`。∎

**去隨機化：條件期望值法**。依序決定每個點放哪一側。設前面的點已經放好，對下一個點 v 有兩個選擇；「剩下的點仍然隨機放」時切邊數的條件期望值，是這兩個選擇的條件期望值的平均，所以至少有一個選擇不會讓條件期望值下降。由於還沒決定的邊貢獻固定是 1/2，比較兩個選擇時只要看 v 與**已決定**鄰居之間的邊：把 v 放到「已決定鄰居較少」的那一側。最後所有點都決定了，條件期望值就是實際的切邊數，而它從頭到尾沒有低於 m/2。

```python
import random
from itertools import product

import networkx as nx


def cut_size(adj, side):
    return sum(1 for u in adj for v in adj[u] if u < v and side[u] != side[v])


def derandomized_cut(adj):
    """條件期望值法：逐點決定放哪邊，每次選讓「已決定的切邊數」較大的一側。"""
    side = {}
    for v in sorted(adj):
        left = sum(1 for w in adj[v] if side.get(w) == 1)    # v 放 0 時，被切到的已決定鄰居
        right = sum(1 for w in adj[v] if side.get(w) == 0)
        side[v] = 0 if left >= right else 1
    return side


def brute_max_cut(adj):
    nodes = sorted(adj)
    best = 0
    for bits in product((0, 1), repeat=len(nodes) - 1):     # 固定第一個點在 0 側，省一半
        side = dict(zip(nodes, (0,) + bits))
        best = max(best, cut_size(adj, side))
    return best


rng = random.Random(31)
print(f"{'graph':16s} {'m':>4s} {'E=m/2':>6s} {'avg 1000 rnd':>12s} {'derand':>6s} {'max':>4s}")
for name, G in [("Petersen", nx.petersen_graph()),
                ("K7", nx.complete_graph(7)),
                ("G(14,.3) s=2", nx.gnp_random_graph(14, 0.3, seed=2)),
                ("C9", nx.cycle_graph(9))]:
    adj = {u: set(G[u]) for u in G}
    m = G.number_of_edges()
    avg = sum(cut_size(adj, {v: rng.randint(0, 1) for v in adj}) for _ in range(1000)) / 1000
    d = cut_size(adj, derandomized_cut(adj))
    best = brute_max_cut(adj)
    assert d >= m / 2 and best >= d
    print(f"{name:16s} {m:4d} {m / 2:6.1f} {avg:12.2f} {d:6d} {best:4d}")
```

輸出（seed = 31）：

```text
graph               m  E=m/2 avg 1000 rnd derand  max
Petersen           15    7.5         7.52     12   12
K7                 21   10.5        10.64     12   12
G(14,.3) s=2       20   10.0        10.08     15   17
C9                  9    4.5         4.45      8    8
```

三個層次：隨機切割的平均值貼著 m/2（理論值）；去隨機化的貪婪版本保證 ≥ m/2，實際上常常好很多（Petersen、K7、C9 都達到最佳）；但它不保證最佳（`G(14, .3)` 得 15，最佳是 17）。最大切割是 NP-hard 的，m/2 是一個 1/2-近似的保證；Goemans–Williamson 的半正定規劃把它推到約 0.878。

### 例二：Caro–Wei 獨立集下界

**定理 31.2（Caro–Wei）**：任何圖的獨立數 `α(G) ≥ Σ_v 1/(deg(v) + 1)`。

**證明**：把點隨機排列（所有 n! 種等機率）。令 S = 「排在自己所有鄰居前面」的點。S 是獨立集：若 u、v 相鄰且都在 S，則 u 在 v 前面且 v 在 u 前面，矛盾。v ∈ S 若且唯若 v 在「v 與它的 deg(v) 個鄰居」這 `deg(v) + 1` 個點中排第一，機率 `1/(deg(v) + 1)`。由線性性 `E[|S|] = Σ 1/(deg(v)+1)`，存在一個排列使 `|S|` 至少這麼大。∎

對 d-正則圖，這給出 `α ≥ n/(d+1)`；取補圖，等價於 Turán 定理的一個弱版本（Ch 32 會證完整的 Turán 定理）。

```python
import random
from fractions import Fraction

import networkx as nx


def caro_wei_bound(adj):
    return sum(Fraction(1, len(adj[v]) + 1) for v in adj)


def random_order_independent_set(adj, rng):
    """隨機排列；v 被選中若它排在所有鄰居前面。期望大小 = Σ 1/(deg+1)。"""
    order = sorted(adj)
    rng.shuffle(order)
    rank = {v: i for i, v in enumerate(order)}
    return {v for v in adj if all(rank[v] < rank[w] for w in adj[v])}


# 1) 在所有 <= 7 點的圖上驗證 alpha(G) >= Caro–Wei
bad = 0
for G in nx.graph_atlas_g()[1:]:
    adj = {u: set(G[u]) for u in G}
    alpha = len(max(nx.find_cliques(nx.complement(G)), key=len))
    bad += alpha < caro_wei_bound(adj)
print("atlas graphs violating alpha >= sum 1/(d+1):", bad)

# 2) 在一張較大的隨機圖上，看隨機排列演算法的平均值是否貼著期望值
G = nx.gnp_random_graph(300, 0.05, seed=4)
adj = {u: set(G[u]) for u in G}
rng = random.Random(0)
sizes = [len(random_order_independent_set(adj, rng)) for _ in range(2000)]
S = random_order_independent_set(adj, rng)
assert all(not (adj[u] & S) for u in S)          # 確實是獨立集
print(f"Caro-Wei expectation = {float(caro_wei_bound(adj)):.3f}, "
      f"empirical mean over 2000 runs = {sum(sizes) / len(sizes):.3f}, max = {max(sizes)}")
```

輸出：

```text
atlas graphs violating alpha >= sum 1/(d+1): 0
Caro-Wei expectation = 19.943, empirical mean over 2000 runs = 19.983, max = 31
```

用 `Fraction` 算下界是刻意的：在 atlas 的小圖上，下界常常恰好等於整數（例如 `K_n` 的 `n · 1/n = 1`），浮點加總可能得到 `0.9999999` 或 `1.0000001`，讓比較結果取決於捨入。

## Erdős 的 Ramsey 下界

**Ramsey 數** `R(k, k)`：最小的 n，使得 `K_n` 的任何紅藍 2-著色都有一個單色的 `K_k`。Ch 32 會證明它存在且 `R(k,k) ≤ 4^k`；這裡證下界。

**定理 31.3（Erdős 1947）**：對 k ≥ 3，`R(k, k) > ⌊2^{k/2}⌋`。

**證明**：令 `n = ⌊2^{k/2}⌋`，把 `K_n` 的每條邊獨立以 1/2 機率塗紅或藍。對每個 k 點子集 A，令 `E_A` 為「A 的 `C(k,2)` 條邊同色」，`P(E_A) = 2 · 2^{−C(k,2)} = 2^{1−C(k,2)}`。由 union bound，

```text
P(存在單色 K_k) ≤ C(n, k) · 2^{1 − C(k,2)} < (n^k / k!) · 2^{1 − k(k−1)/2}
                 ≤ (2^{k²/2} / k!) · 2^{1 − k²/2 + k/2} = 2^{1 + k/2} / k!
```

第一個 `<` 用 `C(n,k) < n^k/k!`（k ≥ 2），`≤` 用 `n ≤ 2^{k/2}`。對 k ≥ 3，`2^{1+k/2} < k!`（k = 3：`2^{2.5} ≈ 5.66 < 6`；之後左邊每次乘 `√2`、右邊每次乘 `k+1 ≥ 4`）。所以機率 < 1，存在一種塗法沒有單色 `K_k`，即 `R(k,k) > n`。∎

**刪點法（alteration）**：與其要求「完全沒有壞結構」，不如允許少量壞結構，再把它們修掉。對任意 m，存在一種塗法，單色 `K_k` 的個數 ≤ 期望值 `E = C(m,k) 2^{1−C(k,2)}`；從每個單色 `K_k` 刪掉一個點，剩下至少 `m − ⌊E⌋` 個點且沒有單色 `K_k`。所以 `R(k,k) > m − ⌊E⌋`，對 m 取最大值。

```python
from fractions import Fraction
from math import comb, floor


def expected_mono(n, k):
    """K_n 隨機 2-著色中，單色 K_k 的期望個數 = C(n,k) * 2^(1 - C(k,2))。"""
    return Fraction(comb(n, k) * 2, 2 ** comb(k, 2))


print(f"{'k':>3s} {'Erdos n':>8s} {'floor 2^(k/2)':>13s} {'alteration n':>12s}")
for k in range(3, 13):
    n = k
    while expected_mono(n + 1, k) < 1:          # 最大的 n 使期望值 < 1  =>  R(k,k) > n
        n += 1
    # 刪點法：存在著色使單色 K_k 數 <= floor(E)，每個刪一點 => R(k,k) > m - floor(E)
    alt = max(m - floor(expected_mono(m, k)) for m in range(k, 4 * n))
    print(f"{k:3d} {n:8d} {floor(2 ** (k / 2)):13d} {alt:12d}")
```

輸出：

```text
  k  Erdos n floor 2^(k/2) alteration n
  3        3             2            3
  4        6             4            6
  5       11             5           11
  6       17             8           18
  7       27            11           29
  8       42            16           47
  9       65            22           74
 10      100            32          116
 11      152            45          179
 12      231            64          276
```

（`Erdos n` 欄是直接解 `C(n,k) 2^{1−C(k,2)} < 1` 的最大 n，比定理裡的 `⌊2^{k/2}⌋` 好，因為定理為了寫出閉式把 `C(n,k)` 放得很鬆。）

**和真實值比**：已知 `R(3,3) = 6`、`R(4,4) = 18`（Ch 32 會用程式驗證前者並用 Paley 圖驗證後者的下界），而 `R(5,5)` 至今只知道在 43 到 46 之間。機率方法給的 `R(4,4) > 6`、`R(5,5) > 11` 在小 k 上很弱——它的價值是**漸近的**：`2^{k/2}` 的指數成長率，至今沒有人能用明確構造達到。

## 大 girth 與大色數

直覺上，色數高是因為圖裡有很多「局部擁擠」的結構（例如三角形、短圈）。Erdős 1959 年用機率方法證明這個直覺是錯的：

**定理 31.4（Erdős 1959）**：對任意 g 與 k，存在一張圖，girth（最短圈長度）> g 且色數 > k。

也就是說，一張圖可以在**每一個點的周圍**都看起來像一棵樹（半徑 g/2 內沒有圈），整體卻需要很多顏色。色數是一個**全域**性質。

**這裡只給證明架構**（每一步的計算都是本章的工具，但把 o(1) 項處理乾淨要一頁不等式）：

```text
1. 取 G(n, p)，p = n^{θ−1}，其中 0 < θ < 1/g。
2. 數短圈：長度 ≤ g 的圈個數 X，期望值 E[X] = Σ_{i=3}^{g} (n)_i / (2i) · p^i ≤ Σ_{i≤g} n^{θi}
   = O(n^{θg}) = o(n)。由 Markov 不等式，P(X ≥ n/2) → 0。
3. 數大獨立集：令 t = ⌈(3/p) ln n⌉，P(α(G) ≥ t) ≤ C(n,t) (1−p)^{C(t,2)} ≤ (n e^{−p(t−1)/2})^t → 0。
4. 對 n 夠大，存在一張圖同時有 X < n/2 且 α < t。
5. 刪點（alteration）：從每個短圈刪一個點，剩下 ≥ n/2 個點，girth > g，且 α 不會變大。
6. 色數 ≥ 點數 / α ≥ (n/2) / t = Θ(n^θ / ln n) → ∞，對 n 夠大就 > k。
```

第 2 步用的是**一階矩**（期望值小 ⇒ 很少發生），第 3 步是 union bound，第 5 步是刪點法——三個工具一起上。這個結果在系統上有個實際含義：Ch 17 的暫存器分配裡，「局部看起來很稀疏」的干涉圖不保證好著色；能保證好著色的是 chordal 這類**全域**結構（Ch 16）。

## Lovász Local Lemma

union bound 要求 `Σ P(壞事件) < 1`。當壞事件很多（例如 600 個子句，每個違反機率 1/32，總和 18.75）時它就失效了，即使每個壞事件都很罕見。但如果壞事件**大多互相獨立**，直覺上它們全部避開的機率應該是正的——就像獨立事件全不發生的機率是 `Π(1 − p_i) > 0`。Local Lemma 把這個直覺做成定理。

**定理 31.5（Lovász Local Lemma，對稱版；Erdős–Lovász 1975）**：設 `A_1, ..., A_m` 是事件，每個 `P(A_i) ≤ p`，且每個 `A_i` 與「除了至多 d 個之外的所有其他事件」的任意組合都互相獨立。若

```text
e · p · (d + 1) ≤ 1
```

則 `P(沒有任何 A_i 發生) > 0`。（e 是自然對數的底。）

**本章不證**。證明是對「在其他事件都不發生的條件下，`A_i` 發生的條件機率 ≤ 1/(d+1)」做歸納，Alon–Spencer 第 5 章有兩頁的完整版本。

**應用：k-SAT**。每個子句恰有 k 個相異變數。在均勻隨機賦值下，一個子句被違反（所有字面值都為假）的機率是 `2^{−k}`。兩個子句的違反事件，若不共用變數就互相獨立。

**推論 31.6**：若每個子句至多和 d 個其他子句共用變數，且 `e (d+1) ≤ 2^k`，則這個 k-SAT 實例可滿足。

例如 k = 5：`2^5 / e ≈ 11.77`，所以 d ≤ 10 就保證可滿足。若每個變數恰出現 3 次，一個子句的 5 個變數各自再出現 2 次，至多和 10 個其他子句相鄰——剛好滿足條件。

### Moser–Tardos：讓 Local Lemma 跑起來

原始的 Local Lemma 是非構造性的。2010 年 Moser 與 Tardos 證明了一個驚人簡單的演算法在 LLL 條件下會很快找到解：

```text
1. 隨機賦值所有變數
2. 只要還有被違反的子句：任選一個，把它的 k 個變數重新隨機賦值
3. 回傳
```

他們證明期望重抽次數 ≤ `m/d`（量級上，在對稱條件下），整個證明用的是一個「把執行記錄壓縮」的熵論證（entropy compression）。這篇論文得到了 2020 年的 Gödel 獎。

```python
import itertools
import math
import random


def bounded_ksat(n_vars, k, occ, rng):
    """每個變數恰好出現 occ 次的隨機 k-SAT（每個子句 k 個相異變數，符號隨機）。"""
    while True:
        slots = [v for v in range(n_vars) for _ in range(occ)]
        rng.shuffle(slots)
        clauses = [slots[i:i + k] for i in range(0, len(slots), k)]
        if all(len(set(c)) == k for c in clauses):
            return [[(v, rng.random() < 0.5) for v in c] for c in clauses]   # (變數, 是否取反)


def violated(clause, x):
    return all(x[v] == neg for v, neg in clause)       # 每個字面值都為假


def moser_tardos(clauses, n_vars, rng, max_resample=10**6):
    x = [rng.random() < 0.5 for _ in range(n_vars)]
    resamples = 0
    while True:
        bad = [c for c in clauses if violated(c, x)]
        if not bad:
            return x, resamples
        if resamples >= max_resample:
            return None, resamples
        for v, _ in bad[0]:                              # 重抽第一個違反子句的變數
            x[v] = rng.random() < 0.5
        resamples += 1


def dependency_degree(clauses):
    var_sets = [set(v for v, _ in c) for c in clauses]
    return max(sum(1 for j, t in enumerate(var_sets) if j != i and s & t) for i, s in enumerate(var_sets))


rng = random.Random(5)
k, occ, n_vars = 5, 3, 1000
clauses = bounded_ksat(n_vars, k, occ, rng)
d = dependency_degree(clauses)
p = 2 ** -k
print(f"k={k}, m={len(clauses)} clauses, n={n_vars} vars, max dependency degree d={d}")
print(f"LLL condition e*p*(d+1) = {math.e * p * (d + 1):.3f}  (<= 1 ? {math.e * p * (d + 1) <= 1})")
print(f"expected #violated clauses under uniform random assignment = m/2^k = {len(clauses) * p:.2f}")
for seed in range(3):
    x, r = moser_tardos(clauses, n_vars, random.Random(seed))
    assert x is not None and not any(violated(c, x) for c in clauses)
    print(f"  seed {seed}: satisfied after {r} resamplings")

# 邊界：k 個變數上全部 2^k 種子句，不可滿足；LLL 條件也不成立
k2 = 3
full = [[(v, s) for v, s in zip(range(k2), signs)] for signs in itertools.product((False, True), repeat=k2)]
d2 = dependency_degree(full)
print(f"all {len(full)} clauses on {k2} vars: d={d2}, e*p*(d+1) = {math.e * 2 ** -k2 * (d2 + 1):.3f}")
x, r = moser_tardos(full, k2, random.Random(0), max_resample=10_000)
print("  Moser-Tardos result:", "gave up" if x is None else x, f"after {r} resamplings")
```

輸出（instance seed = 5；求解 seed = 0, 1, 2）：

```text
k=5, m=600 clauses, n=1000 vars, max dependency degree d=10
LLL condition e*p*(d+1) = 0.934  (<= 1 ? True)
expected #violated clauses under uniform random assignment = m/2^k = 18.75
  seed 0: satisfied after 27 resamplings
  seed 1: satisfied after 17 resamplings
  seed 2: satisfied after 32 resamplings
all 8 clauses on 3 vars: d=7, e*p*(d+1) = 2.718
  Moser-Tardos result: gave up after 10000 resamplings
```

讀法：

- 隨機賦值平均違反 18.75 個子句——union bound 完全無能為力。但 LLL 條件 0.934 ≤ 1 成立，所以一定可滿足；Moser–Tardos 在 17–32 次重抽內找到解。
- 邊界情況：3 個變數上的全部 8 種子句，每一種賦值恰好違反其中一個，不可滿足。這時 `e p (d+1) = e · (1/8) · 8 = e > 1`，LLL 不適用，演算法也確實停不下來。這顯示 LLL 的常數 e 不是證明的副產品：8 個子句、每個機率 1/8、彼此全部相關，是條件「差一個常數倍」就失效的例子。

**這個例子的限制**：這個實例本來就很容易（子句密度 0.6，遠低於隨機 5-SAT 的可滿足性門檻）；任何 SAT 求解器都秒解。LLL 的價值不在速度，而在**保證**：只要局部依賴有界，不管全域有多少約束，一定有解。

> SAT 求解器（CDCL、WalkSAT）的實作在 [programming/sat_smt](../../programming/sat_smt/README.md) 課程；WalkSAT 的「挑一個違反子句、翻一個變數」和 Moser–Tardos 是近親。

**應用回 Ramsey**：把「A 是單色 `K_k`」當成壞事件，兩個事件相依若且唯若兩個 k 子集共用至少兩個點（共用一條邊）。套 LLL 得到 `R(k,k) > (√2/e)(1 + o(1)) k 2^{k/2}`，比 Erdős 的一階矩版本 `(1/(e√2))(1 + o(1)) k 2^{k/2}` 好兩倍。這個常數因子的改進，是 Spencer 在 1970 年代的結果；就我們所知，之後對角 Ramsey 數的下界至今沒有再實質改進（上界則在 2023 年有突破，見「進階」）。

## 二階矩法

一階矩說「期望值小 ⇒ 很少發生」（Markov：`P(X ≥ 1) ≤ E[X]`）。反方向「期望值大 ⇒ 幾乎一定發生」是**錯的**——X 可能大部分時候是 0，偶爾爆大。要排除這種情況，得控制變異數。

**Chebyshev ⇒ 二階矩法**：對非負整數隨機變數 X，

```text
P(X = 0) ≤ P(|X − E[X]| ≥ E[X]) ≤ Var(X) / E[X]^2
```

所以若 `Var(X) = o(E[X]^2)`，則 `P(X > 0) → 1`。

**定理 31.7（三角形的閾值）**：令 X 為 `G(n, p)` 中三角形的個數（`G(n,p)`：每條邊獨立以機率 p 出現，Ch 33 的主角）。

- 若 `pn → 0`，則 `P(X > 0) → 0`。
- 若 `pn → ∞`，則 `P(X > 0) → 1`。

**證明**：`X = Σ_T I_T`，T 跑遍所有 `C(n,3)` 個三點子集，`I_T` 是「T 的三條邊都在」。

- **一階矩**：`E[X] = C(n,3) p^3 ≤ (pn)^3 / 6 → 0`，由 Markov `P(X ≥ 1) ≤ E[X] → 0`。
- **二階矩**：`Var(X) = Σ_T Var(I_T) + Σ_{T ≠ T'} Cov(I_T, I_{T'})`。若 T、T' 不共用邊（共用 0 或 1 個點），兩者涉及的邊不相交，獨立，協方差為 0。共用 2 個點（一條邊）的有序對有 `C(n,3) · 3 · (n−3)` 個（選 T、選 T 的一條邊、選第三個點），每對 `Cov = E[I_T I_{T'}] − p^6 = p^5 − p^6 ≤ p^5`。於是

```text
Var(X) ≤ E[X] + 3(n−3) C(n,3) p^5
Var(X) / E[X]^2 ≤ 1/E[X] + 3(n−3) / (C(n,3) p)
```

  第一項：`E[X] = C(n,3)p^3 ~ (pn)^3/6 → ∞`，所以 → 0。第二項 `~ 18 / (n^2 p) = 18 / (n · pn)` → 0。由二階矩法 `P(X = 0) → 0`。∎

`p = 1/n` 叫做三角形出現的**閾值**（threshold）。在閾值附近 `p = c/n`，X 近似服從平均 `c^3/6` 的 Poisson 分佈（這裡不證），所以 `P(X = 0) → e^{−c^3/6}`。實測：

```python
from math import comb, exp

import networkx as nx


def has_triangle(G):
    adj = {u: set(G[u]) for u in G}
    return any(adj[u] & adj[v] for u, v in G.edges())


def stats(n, p):
    """三角形數 X 的期望值與變異數（精確公式）。"""
    t = comb(n, 3)
    ex = t * p ** 3
    share_edge_pairs = t * 3 * (n - 3)          # 有序對 (T, T')，恰共用一條邊
    var = t * (p ** 3 - p ** 6) + share_edge_pairs * (p ** 5 - p ** 6)
    return ex, var


n, trials = 400, 400
print(f"n={n}, {trials} trials per row, seeds 0..{trials - 1}")
print(f"{'c = pn':>7s} {'E[X]':>8s} {'Var/E^2':>8s} {'P(X=0) sim':>11s} {'exp(-c^3/6)':>12s} {'Chebyshev P(X=0)<=':>19s}")
for c in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0):
    p = c / n
    ex, var = stats(n, p)
    none = sum(not has_triangle(nx.fast_gnp_random_graph(n, p, seed=s)) for s in range(trials))
    cheb = min(1.0, var / ex ** 2)
    print(f"{c:7.2f} {ex:8.3f} {var / ex ** 2:8.3f} {none / trials:11.3f} {exp(-c ** 3 / 6):12.3f} {cheb:19.3f}")
```

輸出（本機約 2.5 秒）：

```text
n=400, 400 trials per row, seeds 0..399
 c = pn     E[X]  Var/E^2  P(X=0) sim  exp(-c^3/6)  Chebyshev P(X=0)<=
   0.25    0.003  387.077       0.998        0.997               1.000
   0.50    0.021   48.452       0.983        0.979               1.000
   1.00    0.165    6.090       0.855        0.846               1.000
   1.50    0.558    1.821       0.605        0.570               1.000
   2.00    1.323    0.778       0.280        0.264               0.778
   3.00    4.466    0.239       0.007        0.011               0.239
   5.00   20.677    0.057       0.000        0.000               0.057
```

模擬值和 Poisson 近似 `e^{−c³/6}` 在每一列都差不到 0.04（400 次試驗的標準誤約 0.025）。Chebyshev 界在 c ≤ 1.5 時沒有資訊（> 1），c 變大才開始有用，而且一直遠比真實值保守——二階矩法是一個**粗糙但通用**的工具，它告訴你極限是 0 還是 1，不告訴你精確的機率。

## 對比與取捨

| 方法 | 證明的形式 | 需要算 | 典型應用 | 能不能變成演算法 |
| --- | --- | --- | --- | --- |
| 一階矩 / union bound | `E[X] < 1` 或 `Σ P(A_i) < 1` ⇒ 好的存在 | 期望值 | Ramsey 下界、碼的存在 | 有時可用條件期望值法 |
| 平均原理 | `E[X] ≥ t` ⇒ 存在 `X ≥ t` | 期望值 | Max cut、Caro–Wei | 常常可以（條件期望值法） |
| 刪點法 | 先容許少量壞結構再修掉 | 期望值 + 修補代價 | 大 girth 大色數、改進 Ramsey 下界 | 視修補步驟而定 |
| Local Lemma | 壞事件罕見且局部相依 ⇒ 全部避開的機率 > 0 | 機率 + 依賴度 | k-SAT、超圖著色、排程 | 可以（Moser–Tardos） |
| 二階矩 | `Var = o(E²)` ⇒ `X > 0` 幾乎必然 | 期望值 + 協方差 | 子圖閾值、隨機圖性質 | 通常不需要（證的是典型行為） |

**為什麼不總是用 LLL？** LLL 要求局部依賴度有界；Ramsey 問題裡每個 `K_k` 事件和大量其他事件相依，LLL 只改進了常數因子。反過來，union bound 只要算一個總和，在依賴關係複雜時更好用。

## 踩雷集錦

1. **以為線性性需要獨立**：`E[Σ I_i] = Σ E[I_i]` 永遠成立；需要獨立（或至少不相關）的是**變異數**的可加性。二階矩法的全部工作就在協方差項上。
2. **以為 `E[X] → ∞` 就代表 X 幾乎總是正的**：反例：令 H 為「K4 外加一個掛在上面的葉子」（5 點 7 邊）。取 `p = n^{−0.7}`，`E[#H] ~ n^5 p^7 = n^{0.1} → ∞`，但 `E[#K4] ~ n^4 p^6 = n^{−0.2} → 0`，所以幾乎一定沒有 K4，也就沒有 H。H 的期望個數很大，是因為「偶爾出現一個 K4 時，就附帶 Θ(n) 個 H」。真正的閾值由 H 最稠密的子圖決定。
3. **把「機率 > 0」讀成「機率很高」**：Erdős 的 Ramsey 論證給出的是 `P(好) > 0`，在 `n = ⌊2^{k/2}⌋` 時其實好的機率接近 1——但 LLL 給的正機率可以是指數小的，隨機猜一次幾乎一定失敗，這就是為什麼 Moser–Tardos 是突破。
4. **LLL 依賴度數錯**：依賴是指「與其他事件的任意組合獨立」，不是兩兩獨立。k-SAT 裡「不共用變數 ⇒ 獨立」是對的，因為不共用變數的子句看的是不相交的隨機位元；但在自己設計的應用裡，兩兩獨立不夠。
5. **條件期望值法的「條件」算錯**：去隨機化時，比較兩個選擇只看已決定的部分，前提是未決定部分的貢獻與這個選擇無關。在 max cut 裡成立（未決定的邊恆為 1/2）；換到其他問題要重新驗證，否則貪婪的版本可能沒有保證。

## 進階：再往深一層

- **Janson 不等式**：二階矩法只說 `P(X = 0) → 0`；當 X 是很多「弱相依」指示變數的和時，Janson 不等式給出 `P(X = 0) ≤ exp(−μ + Δ/2)` 這種指數級的上界，是證明隨機圖性質「以極高機率成立」的標準工具。
- **熵壓縮（entropy compression）**：Moser–Tardos 的分析方法本身變成了一種證明技巧——證明「演算法跑太久」會讓你能用少於應有的位元數描述一串隨機位元，矛盾。它被用在非重複著色（nonrepetitive coloring）等問題上。
- **Container method**：2010 年代 Balogh–Morris–Samotij 與 Saxton–Thomason 發展的方法，用來數「沒有某種子結構的圖有多少張」，把很多極值問題（Ch 32）的「隨機版本」一網打盡。
- **對角 Ramsey 上界的突破**：上界 `R(k,k) ≤ 4^k` 從 Erdős–Szekeres（1935）起到 2023 年只改進了次指數因子；Campos、Griffiths、Morris、Sahasrabudhe 證明了 `R(k,k) ≤ (4 − ε)^k`，Ch 32 會再提。

## 動手練習

1. 把 `derandomized_cut` 改成「依度數由大到小」的順序決定點，在 `nx.gnp_random_graph(14, 0.3, seed=s)`（s = 0..49）上和原版比較平均切邊數，再和暴力最佳解比較。
2. 用刪點法證明：任何平均度數 `d ≥ 1` 的圖，`α(G) ≥ n/(2d)`。（提示：每點以機率 p 保留，再從每條保留下來的邊刪一個端點；對 p 最佳化。）把這個界和 Caro–Wei 在 atlas 上比較，哪一個比較強？
3. 在 Moser–Tardos 程式裡，把 k 改成 4、occ 改成 4（每個子句至多和 12 個子句相鄰，`e · (13) / 16 > 1`），LLL 條件不成立。跑 20 個 instance seed，看演算法是否仍然很快結束，並解釋為什麼這不和 LLL 矛盾。
4. 推導 `G(n, p)` 中 K4 個數 Y 的 `E[Y]` 與 `Var(Y)`（要分共用 2 點與共用 3 點兩種情況），證明閾值是 `p = n^{−2/3}`，再用模擬驗證。

## 本章重點整理

- 機率方法證的是確定性命題：`P(好) > 0` ⇔ 好的物件存在。
- 期望值線性性不需要獨立；平均原理與條件期望值法讓很多存在性證明直接變成貪婪演算法。
- Erdős：`R(k,k) > 2^{k/2}`；刪點法與 LLL 各改進常數，但明確構造至今追不上指數成長。
- 大 girth 大色數：色數是全域性質，局部像樹的圖也可以要很多顏色。
- LLL：罕見且局部相依的壞事件可以全部避開；Moser–Tardos 讓它成為演算法。
- 二階矩法：`Var = o(E²)` ⇒ `P(X > 0) → 1`；三角形在 `p = 1/n` 出現。

## 自我檢核

- [ ] 不看筆記，在五分鐘內寫出 Erdős Ramsey 下界的完整證明，包括 `2^{1+k/2} < k!` 那一步。
- [ ] 如果有人說「E[X] 趨近無窮，所以 X 幾乎一定大於 0」，你能給出一個具體的圖論反例嗎？
- [ ] 能解釋 LLL 條件裡的 e 為什麼不能拿掉，並給出一個「條件差一點就失效」的實例。
- [ ] 能說出條件期望值法在 max cut 上成立的關鍵前提，以及換到別的問題時要檢查什麼。
- [ ] 想一個你工作中的隨機化設計（負載平衡的隨機挑選、hash 分片、隨機退避）：它的保證是「期望值」「高機率」還是「機率 > 0」？哪一種對你的系統才夠用？

## 延伸閱讀

### 書籍

- **《The Probabilistic Method》（第 4 版）** — Noga Alon、Joel H. Spencer（Wiley，2016）
  - **這本書的定位**：這個領域的標準教科書，README 的「讀完本課之後」也推薦它。每章結尾有一篇「The Probabilistic Lens」短文，各自是一個漂亮的獨立論證。
  - **讀哪幾章**：Chapter 1「The Basic Method」（Erdős Ramsey 下界）、Chapter 2「Linearity of Expectation」（max cut、Caro–Wei 一類）、Chapter 3「Alterations」（刪點法、大 girth 大色數）、Chapter 4「The Second Moment」（閾值）、Chapter 5「The Local Lemma」（LLL 的證明與應用）。
  - **前提知識**：大學機率；本章。

### 論文

- **[Some remarks on the theory of graphs](https://emis.dsd.sztaki.hu/classics/Erdos/1947.htm)** — Paul Erdős，Bulletin of the AMS 53（1947），292–294
  - **核心貢獻**：Ramsey 數的指數下界，機率方法的開山之作。
  - **讀哪裡**：全文三頁；下界的論證在後半，原文是用計數寫的（「圖的總數 vs 含 k-團或 k-獨立集的圖數」），和本章的機率寫法一一對應。連結是 Zentralblatt 的經典文獻頁。
  - **和本章的關聯**：定理 31.3。
- **[Graph theory and probability](https://doi.org/10.4153/CJM-1959-003-9)** — Paul Erdős，Canadian Journal of Mathematics 11（1959），34–38
  - **核心貢獻**：大 girth 大色數的存在性，刪點法的經典範例。
  - **讀哪裡**：全文 5 頁；可以對照本章的六步架構逐步讀。
  - **和本章的關聯**：定理 31.4。
- **[Problems and results on 3-chromatic hypergraphs and some related questions](https://old.renyi.hu/~p_erdos/1975-34.pdf)** — Paul Erdős、László Lovász，收錄於 *Infinite and Finite Sets*（Colloq. Math. Soc. János Bolyai 10，North-Holland，1975），609–627
  - **核心貢獻**：Local Lemma 最早出現的地方（為了超圖 2-著色問題）。
  - **讀哪裡**：引理本身與它在超圖著色上的應用；連結是 Rényi 研究所的 Erdős 論文庫。
  - **和本章的關聯**：定理 31.5。
- **[A constructive proof of the general Lovász Local Lemma](https://doi.org/10.1145/1667053.1667060)** — Robin A. Moser、Gábor Tardos，Journal of the ACM 57(2)（2010）
  - **核心貢獻**：證明「重抽違反事件的變數」這個簡單演算法在 LLL 條件下期望多項式時間結束。
  - **讀哪裡**：Section 1–2（演算法與主定理）；證明用的「witness tree」論證值得慢慢讀。arXiv 版本 0903.0544 免費。
  - **和本章的關聯**：本章的 Moser–Tardos 實作。

機率方法說「隨機的東西幾乎都很好」。下一章換一個角度問：一個結構要多大，才能保證**無論如何**都包含某個子結構？這是 Ramsey 理論與極值圖論——本章的 Ramsey 下界會在那裡遇到它的上界。

→ [Ch 32 Ramsey 理論與極值圖論](./32-ramsey-extremal.md)
