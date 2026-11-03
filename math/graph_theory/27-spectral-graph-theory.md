# Ch 27 — 譜圖論入門

> **目標**：把一張圖寫成鄰接矩陣，用它的特徵值回答組合問題——`A^k` 為什麼在數 walk、最大特徵值為什麼被夾在平均度數與最大度數之間、二分圖的譜為什麼正負對稱；並且能用 NumPy 實算常見圖族的譜，對照推導出的公式。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、SciPy 1.18.1，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

到 Part 4 為止，我們分析圖的方法都是組合式的：數邊、找路徑、構造增廣路、做歸納。這些方法精準，但常常要對每個問題重新發明一套論證。

譜圖論（spectral graph theory）換了一個角度：把圖變成一個矩陣，再借用線性代數兩百年累積的工具。歷史上這條路有兩個源頭。一個是量子化學——1930 年代 Hückel 的分子軌域近似裡，碳骨架的能階就是一個圖的鄰接矩陣特徵值；另一個是數學家在 1950 年代開始系統研究「圖的譜」（一般把 Collatz 與 Sinogowitz 1957 年的論文視為起點）。真正讓它變成 CS 的主流工具，是 1980 年代以後的三件事：expander（Ch 30）、譜分群（Ch 28、練習 E）、以及 PageRank（Ch 29）。

為什麼要多學一套語言？因為有些問題用組合方法很難碰，用特徵值卻一行就有界：

- 一張圖有多少條長度 k 的 walk？`A^k` 的元素。
- 這張圖「多像」正則圖？看最大特徵值離平均度數多遠。
- 這張圖有沒有奇環？看譜是否正負對稱。
- 這張圖至少要幾種顏色？Hoffman 界給一個下界，不必窮舉著色。

代價是：特徵值是一個**全域**、**連續**的量，它把整張圖壓成 n 個實數，資訊必然有損失。本章最後會看到兩張不同構卻同譜的圖——譜不是完整的圖不變量。

## 先建立直覺

把鄰接矩陣 `A` 想成一個「傳話」的運算子。給每個點一個數字 `x_v`（想成每個人手上的錢），`A` 作用一次，每個點拿到的新值是**鄰居們舊值的總和**：

```text
   x = (x_0, x_1, x_2, x_3)            (Ax)_v = Σ_{w 與 v 相鄰} x_w

      0 ─── 1                           (Ax)_0 = x_1 + x_3
      │     │                           (Ax)_1 = x_0 + x_2
      3 ─── 2                           (Ax)_2 = x_1 + x_3
                                         (Ax)_3 = x_0 + x_2
```

重複作用 k 次，就是把「1 塊錢放在 s」沿著邊擴散 k 輪，最後落在 t 的量，正好是從 s 走到 t 的 k 步 walk 數。

特徵向量是什麼？是那些「傳話一輪後形狀不變、只被放大 λ 倍」的分配方式。全 1 向量在 d-正則圖上就是一個：每個人都拿到 d 個鄰居各 1 塊，總共 d 塊，形狀不變，放大 d 倍。最大特徵值衡量「擴散的放大速度」；而一個向量在二分圖兩側一正一負時，每傳一輪就整個翻號——這就是負特徵值的來源。

> 如果你對圖的表示法（鄰接矩陣 vs 鄰接串列）還不熟，先回看 [Ch 7 — 圖的定義、表示與同構](./07-graph-definitions.md) 的「表示法」一節。

## 鄰接矩陣與 walk 計數

對一張 n 個點的簡單無向圖，鄰接矩陣（adjacency matrix）`A` 是 n×n 的 0/1 矩陣，`A[u][v] = 1` 若且唯若 `uv` 是一條邊。因為無向，`A` 是**對稱**的；因為沒有自環，對角線全是 0。

**walk** 是一串點 `v_0, v_1, ..., v_k`，相鄰兩點之間有邊，允許重複點與重複邊；長度是邊數 k。（path 不能重複點，這兩個詞在 Ch 7 有區分。）

**定理 27.1（walk 計數）**：`(A^k)[s][t]` 等於從 s 到 t、長度為 k 的 walk 數。

**證明**：對 k 歸納。k = 0 時 `A^0 = I`，長度 0 的 walk 只有「停在原地」，s = t 時 1 條、否則 0 條，成立。假設對 k 成立。一條長度 k+1、從 s 到 t 的 walk，依「倒數第二個點 w」分類：它是一條 s 到 w 的 k 步 walk，再接一條邊 `wt`。不同的 w 給出不相交的類別，所以

```text
#walks_{k+1}(s, t) = Σ_w #walks_k(s, w) · A[w][t] = Σ_w (A^k)[s][w] · A[w][t] = (A^{k+1})[s][t]
```

第二個等號用歸納假設，第三個是矩陣乘法的定義。∎

不要相信證明，跑一次。下面這張 5 點圖是一個 4-cycle `0-1-2-3-0` 加上弦 `0-2`，再在 3 上掛一個葉子 4。我們用 DFS 暴力展開所有 walk，和 `A^k` 對拍：

```python
import numpy as np

# 一張 5 個點的小圖：0-1-2-3-0 是 4-cycle，再加一條弦 0-2 與一個掛在 3 上的葉子 4
adj = {0: {1, 2, 3}, 1: {0, 2}, 2: {0, 1, 3}, 3: {0, 2, 4}, 4: {3}}
n = len(adj)

A = np.zeros((n, n), dtype=np.int64)
for u in adj:
    for v in adj[u]:
        A[u, v] = 1


def count_walks(adj, s, t, k):
    """暴力展開：從 s 走 k 步停在 t 的 walk 有幾條（walk 可重複點與邊）。"""
    if k == 0:
        return 1 if s == t else 0
    return sum(count_walks(adj, w, t, k - 1) for w in adj[s])


for k in range(1, 6):
    Ak = np.linalg.matrix_power(A, k)
    brute = np.array([[count_walks(adj, s, t, k) for t in range(n)] for s in range(n)])
    assert (Ak == brute).all()
    print(f"k={k}: A^k == brute force  | 0->2 walks = {Ak[0, 2]:3d} | trace = {np.trace(Ak)}")

A2, A3 = A @ A, A @ A @ A
print("diag(A^2) =", np.diag(A2), " degrees =", A.sum(axis=1))
print("trace(A^3)/6 =", np.trace(A3) // 6, "triangles")
```

輸出：

```text
k=1: A^k == brute force  | 0->2 walks =   1 | trace = 0
k=2: A^k == brute force  | 0->2 walks =   2 | trace = 12
k=3: A^k == brute force  | 0->2 walks =   5 | trace = 12
k=4: A^k == brute force  | 0->2 walks =  15 | trace = 60
k=5: A^k == brute force  | 0->2 walks =  35 | trace = 110
diag(A^2) = [3 2 3 3 1]  degrees = [3 2 3 3 1]
trace(A^3)/6 = 2 triangles
```

三個值得記住的推論，全都直接從定理 27.1 讀出來：

| 量 | 等於 | 為什麼 |
| --- | --- | --- |
| `A^2[v][v]` | `deg(v)` | 從 v 出去再回來的 2 步 walk，一條鄰邊對應一條 |
| `trace(A^2)` | `2\|E\|` | 把上一列加總，就是 handshake lemma |
| `trace(A^3)` | `6 × 三角形數` | 長度 3 的閉 walk 必是三角形；每個三角形有 3 個起點 × 2 個方向 |

這裡的兩個三角形是 `{0,1,2}` 和 `{0,2,3}`。注意 `trace(A^4)` **不是** 8 倍的 4-cycle 數：長度 4 的閉 walk 還包含 `0-1-0-1-0` 這種來回走同一條邊的退化 walk。這是用 trace 數子圖時最常見的錯，後面踩雷區會再提。

## 線代補課：對稱矩陣的譜定理與 Rayleigh 商

本章後面所有論證都靠兩個工具。讀者若只學過「特徵值是 `det(A - λI) = 0` 的根」，這一節把需要的部分補齊，而且給完整證明，因為 Ch 28–30 會一再用到。

**定理 27.2（實對稱矩陣的譜定理）**：若 `A` 是 n×n 實對稱矩陣，則存在 n 個實數 `λ_1 ≥ λ_2 ≥ ... ≥ λ_n` 與一組**正交歸一**（orthonormal）向量 `v_1, ..., v_n`，使得 `A v_i = λ_i v_i`。等價地，`A = Σ λ_i v_i v_i^T`。

**證明**分三步。

1. **特徵值是實數。** 由代數基本定理，`det(A - λI)` 在複數上至少有一個根 λ，對應複向量 `x ≠ 0` 使 `Ax = λx`。令 `x*` 為共軛轉置。`x* A x = λ x* x`。對左邊取共軛：因為 `A` 是實對稱，`(x* A x)* = x* A* x = x* A x`，所以 `x* A x` 是實數；`x* x = Σ|x_i|^2 > 0` 也是實數，於是 λ 是實數。λ 實數之後，`A - λI` 是實矩陣且奇異，所以存在**實**的特徵向量。
2. **不同特徵值的特徵向量互相正交。** 若 `Ax = λx`、`Ay = μy`、`λ ≠ μ`，則 `λ (x·y) = (Ax)·y = x·(A^T y) = x·(Ay) = μ (x·y)`，中間用到 `A^T = A`。所以 `(λ - μ)(x·y) = 0`，得 `x·y = 0`。
3. **湊滿 n 個。** 對 n 歸納。取一個實特徵向量 `v_1`（步驟 1），單位化。令 `W = {y : y·v_1 = 0}`，維度 n−1。對 `y ∈ W`，`v_1·(Ay) = (A v_1)·y = λ_1 (v_1·y) = 0`，所以 `A` 把 `W` 送回 `W`。在 `W` 上取一組正交歸一基，`A` 限制在 `W` 上的矩陣仍然是對稱的，由歸納假設它在 `W` 裡有 n−1 個正交歸一特徵向量，加上 `v_1` 就湊滿。∎

**Rayleigh 商**：對非零向量 x，定義 `R(x) = (x^T A x) / (x^T x)`。

**定理 27.3**：`λ_n ≤ R(x) ≤ λ_1`；上界在 `x = v_1` 達到，而且 `R(x) = λ_1` 若且唯若 x 是 `λ_1` 的特徵向量。更進一步，若限制 `x ⟂ v_1`，則 `R(x) ≤ λ_2`。

**證明**：把 x 用正交歸一基展開，`x = Σ c_i v_i`。則 `Ax = Σ c_i λ_i v_i`，由正交性 `x^T A x = Σ λ_i c_i^2`、`x^T x = Σ c_i^2`。所以 `R(x)` 是 `λ_i` 以 `c_i^2` 為權重的**加權平均**，必落在最小與最大之間；等於 `λ_1` 若且唯若權重全部落在 `λ_i = λ_1` 的那些 i 上，也就是 x 落在 `λ_1` 的特徵空間裡。若 `x ⟂ v_1`，則 `c_1 = 0`，加權平均只在 `λ_2, ..., λ_n` 之間，所以 `≤ λ_2`。∎

這個「加權平均」的圖像是整個 Part 5 最重要的心智模型：**想要 λ_1 的下界，隨便猜一個測試向量 x 算 `R(x)` 就有了**；想要上界，才需要真的分析特徵向量。

> 對圖來說，`x^T A x = Σ_{u,v} A[u][v] x_u x_v = 2 Σ_{uv ∈ E} x_u x_v`。每條邊在對稱矩陣裡出現兩次，這個 2 很容易漏。

## 特徵值界住度數

**定理 27.4**：令 `d_avg = 2|E|/n`、`Δ` 為最大度數。則 `max(d_avg, √Δ) ≤ λ_1 ≤ Δ`；而且所有特徵值都滿足 `|λ_i| ≤ Δ`。

**證明**：

- **`λ_1 ≥ d_avg`**：取測試向量 `x = 1`（全 1）。`x^T A x = Σ_{u,v} A[u][v] = 2|E|`，`x^T x = n`，由定理 27.3，`λ_1 ≥ R(1) = 2|E|/n`。
- **`λ_1 ≥ √Δ`**：令 c 為度數 Δ 的點。測試向量取 `x_c = √Δ`、c 的每個鄰居 `x_w = 1`、其他點 0。則 `x^T x = Δ + Δ = 2Δ`；`x^T A x = 2 Σ_{uv∈E} x_u x_v` 中，c 與每個鄰居的邊貢獻 `√Δ · 1`，共 Δ 條，鄰居之間若有邊再多貢獻非負的量，所以 `x^T A x ≥ 2Δ√Δ`，`R(x) ≥ √Δ`。
- **`|λ| ≤ Δ`**：設 `Ax = λx`，取 u 使 `|x_u|` 最大。`|λ| |x_u| = |(Ax)_u| = |Σ_{w~u} x_w| ≤ deg(u) |x_u| ≤ Δ |x_u|`，兩邊除以 `|x_u| > 0`。∎

在幾種圖上實算：

```python
import networkx as nx
import numpy as np


def spectrum(G):
    A = nx.to_numpy_array(G, nodelist=sorted(G))
    return np.linalg.eigvalsh(A)[::-1]          # 由大到小


graphs = {
    "path P10": nx.path_graph(10),
    "star K1,9": nx.star_graph(9),
    "Petersen": nx.petersen_graph(),
    "K5": nx.complete_graph(5),
    "G(30,0.2) seed=7": nx.gnp_random_graph(30, 0.2, seed=7),
}

print(f"{'graph':18s} {'avg deg':>8s} {'lambda1':>8s} {'max deg':>8s} {'sqrt(max)':>9s}")
for name, G in graphs.items():
    lam = spectrum(G)
    degs = [d for _, d in G.degree()]
    avg, dmax = 2 * G.number_of_edges() / G.number_of_nodes(), max(degs)
    assert avg - 1e-9 <= lam[0] <= dmax + 1e-9
    assert lam[0] >= np.sqrt(dmax) - 1e-9
    print(f"{name:18s} {avg:8.3f} {lam[0]:8.3f} {dmax:8d} {np.sqrt(dmax):9.3f}")
```

輸出：

```text
graph               avg deg  lambda1  max deg sqrt(max)
path P10              1.800    1.919        2     1.414
star K1,9             1.800    3.000        9     3.000
Petersen              3.000    3.000        3     1.732
K5                    4.000    4.000        4     2.000
G(30,0.2) seed=7      6.800    7.734       14     3.742
```

讀這張表：

- **正則圖**（Petersen、K5）三個量重合：`d_avg = λ_1 = Δ`。對連通圖，`λ_1 = Δ` 若且唯若圖是 Δ-正則（這個方向的證明要用 Perron–Frobenius，本章不證）。
- **星形**把兩個界拉得最開：平均度數 1.8、最大度數 9，`λ_1 = 3 = √9` 正好卡在 `√Δ` 下界上。λ_1 被一個「樞紐點」拉高，但拉不到 Δ。
- **隨機圖** `λ_1 = 7.73` 比平均度數 6.8 稍大。λ_1 可以看成一種「加權過的平均度數」，度數不均勻時會往高度數的點偏。

**為什麼不直接用平均度數？** 因為 λ_1 才是控制 walk 數成長率的量：`trace(A^k) = Σ λ_i^k ≈ λ_1^k`（k 大時）。在傳染病模型、資訊擴散、甚至 Ch 29 的隨機遊走裡，決定「會不會爆發」的門檻是 λ_1，不是平均度數——一張平均度數低但有超級樞紐的網路，擴散速度由樞紐決定。

## 二分圖的譜是正負對稱的

**定理 27.5**：圖 G 是二分圖，若且唯若它的譜（含重數）對 0 對稱，也就是 λ 是特徵值時 −λ 也是，而且重數相同。

**證明（⇒）**：設二分為 `(X, Y)`。若 `Ax = λx`，定義 y：`y_v = x_v`（v ∈ X）、`y_v = −x_v`（v ∈ Y）。每條邊一端在 X、一端在 Y，所以對 v ∈ X，`(Ay)_v = Σ_{w~v} y_w = −Σ_{w~v} x_w = −λ x_v = −λ y_v`；對 v ∈ Y 同理。所以 y 是 −λ 的特徵向量。翻號是可逆的線性映射，把 λ 的特徵空間一對一送到 −λ 的特徵空間，重數相同。

**證明（⇐）**：若譜對 0 對稱，對任意奇數 k，`trace(A^k) = Σ λ_i^k = 0`（λ 與 −λ 成對相消）。由定理 27.1，`trace(A^k)` 是長度 k 的閉 walk 數，所以 G 沒有任何奇數長度的閉 walk，特別是沒有奇環。沒有奇環的圖是二分圖（Ch 7 的定理）。∎

```python
import networkx as nx
import numpy as np


def spectrum(G):
    A = nx.to_numpy_array(G, nodelist=sorted(G))
    return np.sort(np.linalg.eigvalsh(A))


def symmetric(lam):
    return np.allclose(np.sort(lam), np.sort(-lam), atol=1e-9)


cases = {
    "C6": nx.cycle_graph(6),
    "C7": nx.cycle_graph(7),
    "Q3": nx.hypercube_graph(3),
    "Petersen": nx.petersen_graph(),
    "random tree": nx.random_labeled_tree(9, seed=1),
    "K4": nx.complete_graph(4),
}
for name, G in cases.items():
    lam = spectrum(G)
    print(f"{name:12s} bipartite={nx.is_bipartite(G)!s:5s} symmetric={symmetric(lam)!s:5s} "
          f"lam_max={lam[-1]:+.3f} lam_min={lam[0]:+.3f}")

# 翻號手法：二分圖 (X, Y)，把特徵向量在 Y 那側變號，得到 -lambda 的特徵向量
G = nx.cycle_graph(6)
A = nx.to_numpy_array(G, nodelist=range(6))
lam, vecs = np.linalg.eigh(A)
x = vecs[:, -2]                       # 對應第二大特徵值
s = np.array([1 if v % 2 == 0 else -1 for v in range(6)])   # X = 偶數點, Y = 奇數點
y = s * x
print("A x = lam x ?", np.allclose(A @ x, lam[-2] * x), " A y = -lam y ?", np.allclose(A @ y, -lam[-2] * y))
```

輸出：

```text
C6           bipartite=True  symmetric=True  lam_max=+2.000 lam_min=-2.000
C7           bipartite=False symmetric=False lam_max=+2.000 lam_min=-1.802
Q3           bipartite=True  symmetric=True  lam_max=+3.000 lam_min=-3.000
Petersen     bipartite=False symmetric=False lam_max=+3.000 lam_min=-2.000
random tree  bipartite=True  symmetric=True  lam_max=+2.236 lam_min=-2.236
K4           bipartite=False symmetric=False lam_max=+3.000 lam_min=-1.000
```

最後一行輸出是 `A x = lam x ? True  A y = -lam y ? True`，翻號手法在數值上成立。

一個更細的版本在 Ch 29 會用到：對**連通**圖，`λ_n = −λ_1` 若且唯若 G 是二分圖。C7 的 `λ_min = −1.802` 離 −2 不遠——奇環很長時，圖「幾乎」是二分的，這個差距正是隨機遊走在 C7 上收斂很慢的原因。

## 常見圖族的譜：推導並用 NumPy 對照

每一個公式都能用「猜特徵向量、驗證 `Ax = λx`」推出來：

| 圖 | 特徵值 | 推導關鍵 |
| --- | --- | --- |
| 完全圖 `K_n` | `n−1`（1 次）、`−1`（n−1 次） | `A = J − I`；全 1 矩陣 J 的特徵值是 n（向量 1）與 0（所有 ⟂ 1 的向量） |
| 圈 `C_n` | `2cos(2πk/n)`，k = 0..n−1 | 試 `x_j = ω^{jk}`，`ω = e^{2πi/n}`：`(Ax)_j = (ω^k + ω^{−k}) x_j` |
| 路徑 `P_n` | `2cos(πk/(n+1))`，k = 1..n | 試 `x_j = sin(πkj/(n+1))`，j = 1..n；`sin(a−b)+sin(a+b) = 2 sin a cos b`，邊界 `x_0 = x_{n+1} = 0` 自動成立 |
| 星 `K_{1,n−1}` | `±√(n−1)`、`0`（n−2 次） | 秩為 2；中心 `√(n−1)`、葉子全 1 |
| 超立方體 `Q_d` | `d − 2k`，重數 `C(d,k)` | `Q_d` 是 d 個 `K_2` 的笛卡兒積，積圖的特徵值相加；`K_2` 的特徵值是 ±1 |
| 完全二分圖 `K_{m,n}` | `±√(mn)`、`0`（m+n−2 次） | 秩為 2；兩側各取常數 |

路徑那一列值得多看一眼：圈的特徵向量是「繞一圈的波」，路徑的特徵向量是「兩端固定的駐波」，和弦振動是同一個數學。用 NumPy 對照：

```python
from math import comb, cos, pi, sqrt

import networkx as nx
import numpy as np


def spectrum(G):
    A = nx.to_numpy_array(G, nodelist=sorted(G))
    return np.sort(np.linalg.eigvalsh(A))[::-1]


def check(name, G, formula):
    got = spectrum(G)
    want = np.sort(np.array(formula, dtype=float))[::-1]
    err = np.max(np.abs(got - want))
    print(f"{name:10s} max|numpy - formula| = {err:.1e}   top3 = {np.round(got[:3], 4)}")
    assert err < 1e-9


n = 8
check("K8", nx.complete_graph(n), [n - 1] + [-1] * (n - 1))
check("C8", nx.cycle_graph(n), [2 * cos(2 * pi * k / n) for k in range(n)])
check("P8", nx.path_graph(n), [2 * cos(pi * k / (n + 1)) for k in range(1, n + 1)])
check("K1,7", nx.star_graph(n - 1), [sqrt(n - 1), -sqrt(n - 1)] + [0] * (n - 2))
d = 4
check("Q4", nx.hypercube_graph(d),
      [d - 2 * k for k in range(d + 1) for _ in range(comb(d, k))])
check("K3,5", nx.complete_bipartite_graph(3, 5), [sqrt(15), -sqrt(15)] + [0] * 6)
```

輸出：

```text
K8         max|numpy - formula| = 1.9e-15   top3 = [ 7. -1. -1.]
C8         max|numpy - formula| = 4.4e-16   top3 = [2.     1.4142 1.4142]
P8         max|numpy - formula| = 6.7e-16   top3 = [1.8794 1.5321 1.    ]
K1,7       max|numpy - formula| = 0.0e+00   top3 = [2.6458 0.     0.    ]
Q4         max|numpy - formula| = 1.3e-15   top3 = [4. 2. 2.]
K3,5       max|numpy - formula| = 2.3e-16   top3 = [3.873 0.    0.   ]
```

誤差都在 `1e-15` 量級，是浮點捨入，不是公式錯。容差我們設 `1e-9`：比機器精度 `2.2e-16` 大很多，足以吸收 n = 8 的累積誤差，又小到任何公式錯誤都會被抓到。

注意 `C8` 的第二、第三大特徵值都是 `1.4142 = 2cos(π/4)`：k 與 n−k 給出同一個 cos 值，所以圈的非極端特徵值都是二重的。重根代表特徵向量**不唯一**——NumPy 回傳的只是特徵空間裡的某一組基，換一台機器、換一版 LAPACK 可能是另一組。要比對特徵向量時，比特徵空間（投影矩陣），不要比個別向量。

## 底層機制：`eigvalsh` 為什麼只能用在對稱矩陣上

前面每個範例都用 `np.linalg.eigvalsh`，而不是 `eigvals`。`eigvalsh` 呼叫 LAPACK 的對稱特徵值求解器：它**假設**輸入是對稱（Hermitian）的，演算法只讀取一半三角形（NumPy 預設讀下三角，參數 `UPLO='L'`），先用正交變換把矩陣化成三對角形，再對三對角矩陣求特徵值。好處是快、穩、保證回傳實數並排序；代價是它**不檢查**輸入是否真的對稱。

```text
           對稱矩陣 A
               │  Householder 正交相似變換（保持特徵值）
               ▼
         三對角矩陣 T
               │  QR / 分治法 / MRRR 迭代
               ▼
        實特徵值 λ_n ≤ ... ≤ λ_1（已排序）
```

故意弄錯一次：把有向 3-cycle 的鄰接矩陣丟給 `eigvalsh`。

```python
import networkx as nx
import numpy as np

# 錯誤示範：有向 3-cycle 0->1->2->0 的鄰接矩陣不是對稱的
D = nx.DiGraph([(0, 1), (1, 2), (2, 0)])
A = nx.to_numpy_array(D, nodelist=[0, 1, 2])
print("A =\n", A.astype(int))
print("eigvalsh (wrong!):", np.round(np.linalg.eigvalsh(A), 4))
print("eigvals  (right) :", np.round(np.linalg.eigvals(A), 4))
```

輸出：

```text
A =
 [[0 1 0]
 [0 0 1]
 [1 0 0]]
eigvalsh (wrong!): [-1.  0.  1.]
eigvals  (right) : [-0.5+0.866j -0.5-0.866j  1. +0.j   ]
```

`eigvalsh` 沒有報錯，安靜地回傳了**另一個矩陣**的特徵值：它只看下三角 `A[2][0] = 1`，等於在算無向路徑 `0-2` 加孤立點 1 的譜 `{−1, 0, 1}`。真正的特徵值是三次單位根 `1, e^{±2πi/3}`——有向圖的鄰接矩陣一般不對稱，特徵值可以是複數，本章的定理（實特徵值、正交基、Rayleigh 商）**全部不適用**。Ch 29 處理有向圖（PageRank）時，我們會換成隨機矩陣與 Perron–Frobenius 的語言。

## 對比與取捨

| 工具 | 回答的問題 | 優點 | 缺點 |
| --- | --- | --- | --- |
| 組合論證（Ch 2–26） | 精確的結構性命題 | 精準、可構造 | 每個問題要重新設計論證 |
| 鄰接譜 `A` | walk 數、度數、二分性、著色界 | 一次計算回答多個問題；正則圖上最乾淨 | 度數不均時被樞紐點主導 |
| Laplacian 譜 `L = D − A`（Ch 28） | 連通性、切割、生成樹數 | 零特徵值直接數連通分量 | 和度數耦合，非正則圖要正規化 |
| 正規化 Laplacian / 轉移矩陣（Ch 28–29） | 隨機遊走、conductance | 度數不均時也公平 | 公式多一層 `D^{−1/2}` |
| 完整同構測試（Ch 37） | 兩圖是否同構 | 精確 | 一般情況沒有已知多項式演算法 |

**為什麼不用特徵值當同構測試？** 因為譜不是完整不變量。最小的反例只有 5 個點：

```python
import networkx as nx
import numpy as np


def spectrum(G):
    return np.round(np.sort(np.linalg.eigvalsh(nx.to_numpy_array(G))), 6)


star = nx.star_graph(4)                                  # K_{1,4}
c4k1 = nx.disjoint_union(nx.cycle_graph(4), nx.empty_graph(1))
print("K1,4    :", spectrum(star))
print("C4 + K1 :", spectrum(c4k1))
print("isomorphic?", nx.is_isomorphic(star, c4k1), "| connected?", nx.is_connected(star), nx.is_connected(c4k1))
```

輸出：

```text
K1,4    : [-2.  0.  0.  0.  2.]
C4 + K1 : [-2.  0.  0.  0.  2.]
isomorphic? False | connected? True False
```

一張連通、一張不連通，鄰接譜卻完全一樣。所以鄰接譜甚至看不出連通性——Ch 28 的 Laplacian 譜才看得出來。反方向是對的：**譜不同 ⇒ 不同構**，所以譜可以當快速的「排除」過濾器，這也是 Ch 37 WL 測試之前常見的前置步驟。

## 踩雷集錦

1. **以為 `trace(A^k)` 在數長度 k 的圈**：它數的是閉 **walk**。k = 3 時閉 walk 恰好都是三角形，所以 `trace(A^3)/6` 對；k = 4 開始混入 `u-v-u-v-u`、`u-v-u-w-u` 這類退化 walk，`trace(A^4)/8` 不等於 4-cycle 數。要數 4-cycle，得把退化項扣掉：`#C4 = (trace(A^4) − 2|E| − 4 Σ_v C(deg(v), 2)) / 8`。
2. **以為 λ_1 就是平均度數**：只有正則圖如此。星形 `K_{1,9}` 平均度數 1.8、λ_1 = 3。把 λ_1 當成「網路的平均連結數」去做容量規劃，會在有樞紐的網路上嚴重低估擴散速度。
3. **對有向圖用 `eigvalsh` 或 `eigh`**：如上所示，它安靜地給錯答案。對稱性要自己保證；拿不準時先 `assert np.allclose(A, A.T)`。
4. **比對特徵向量時忽略符號與重根**：特徵向量乘 −1 仍是特徵向量；重根時整個特徵空間都可以旋轉。兩次計算的 `v_2` 不一樣不代表有 bug。比對時先固定符號（例如讓第一個非零分量為正），重根時比投影矩陣 `V V^T`。
5. **以為同譜就同構**：`K_{1,4}` 與 `C4 + K1` 是反例。反過來，同構一定同譜（換點的編號等於做置換相似 `P A P^T`，特徵值不變）。

## 進階：再往深一層

**Perron–Frobenius 定理**（本章不證）：連通圖的鄰接矩陣，λ_1 是單根，對應一個**每個分量都嚴格為正**的特徵向量，而且 `|λ_i| ≤ λ_1`。這是 Ch 29 隨機遊走收斂、PageRank 有唯一解的根據。其中「嚴格為正」的部分可以自己證：若 `x ≥ 0` 是 λ_1 的特徵向量且 `x_u = 0`，則 `0 = λ_1 x_u = Σ_{w~u} x_w`，所以 u 的鄰居全是 0，沿著連通性擴散到整張圖，矛盾。

**譜與著色**：兩個經典界把 Ch 15 的著色數夾住：

- Wilf（1967）：`χ(G) ≤ 1 + λ_1`。比 Brooks 的 `Δ + 1` 更緊，因為 `λ_1 ≤ Δ`。
- Hoffman（1970）：`χ(G) ≥ 1 − λ_1 / λ_n`。

```python
from itertools import product
from math import ceil, floor

import networkx as nx
import numpy as np


def chromatic_number(G):
    """暴力：從 k=1 往上試所有著色（只適合 n <= 10）。"""
    nodes = sorted(G)
    for k in range(1, len(nodes) + 1):
        for col in product(range(k), repeat=len(nodes)):
            c = dict(zip(nodes, col))
            if all(c[u] != c[v] for u, v in G.edges()):
                return k


cases = {
    "Petersen": nx.petersen_graph(),
    "C7": nx.cycle_graph(7),
    "K4": nx.complete_graph(4),
    "wheel W7": nx.wheel_graph(7),
    "G(9,0.4) s=3": nx.gnp_random_graph(9, 0.4, seed=3),
}
print(f"{'graph':14s} {'Hoffman>=':>9s} {'chi':>4s} {'Wilf<=':>7s} {'Delta+1':>8s}")
for name, G in cases.items():
    lam = np.linalg.eigvalsh(nx.to_numpy_array(G))
    lo = 1 - lam[-1] / lam[0]
    hi = 1 + lam[-1]
    chi = chromatic_number(G)
    dmax = max(d for _, d in G.degree())
    assert ceil(lo - 1e-9) <= chi <= floor(hi + 1e-9)
    print(f"{name:14s} {lo:9.3f} {chi:4d} {hi:7.3f} {dmax + 1:8d}")
```

輸出：

```text
graph          Hoffman>=  chi  Wilf<=  Delta+1
Petersen           2.500    3   4.000        4
C7                 2.110    3   3.000        3
K4                 4.000    4   4.000        4
wheel W7           2.823    3   4.646        7
G(9,0.4) s=3       2.473    3   4.227        7
```

wheel `W7`（一個中心接 6-cycle）最能說明 Wilf 界的價值：`Δ + 1 = 7`，Wilf 給 4.646，真實 χ = 3。這兩個界在暫存器分配（Ch 17）裡沒有直接用處——干涉圖是 chordal 的時候有精確演算法——但它們示範了一個模式：**一個 `O(n^3)` 的特徵值計算，可以給一個 NP-hard 量的兩側界**。Ch 30 的 expander mixing lemma 與 Hoffman 界是同一個論證的不同包裝。

**交錯定理（Cauchy interlacing）**：刪掉一個點，新圖的特徵值 `μ_i` 滿足 `λ_{i+1} ≤ μ_i ≤ λ_i`。這是證明「子圖的 λ_1 不超過原圖的 λ_1」以及很多極值結果的工具；Spielman 的書第 4 章有完整證明。

## 動手練習

1. 不跑程式，用定理 27.1 推出 `K_4` 上從一點出發、長度 3 回到原點的 walk 數；再用 `np.linalg.matrix_power` 驗證。
2. 證明 `K_{m,n}` 的特徵值公式：先說明 `A` 的秩是 2，再對 `x = (a,...,a, b,...,b)` 解 `Ax = λx`。
3. 寫一個函式 `count_c4(G)`，用踩雷 1 的公式由 `trace(A^4)` 數 4-cycle，在 `nx.graph_atlas_g()` 所有 ≤ 7 點的圖上，對拍一個暴力列舉 4 點子集的版本。
4. 在 `nx.cycle_graph(n)` 上，畫出 n = 5..40 的 `λ_1 + λ_n`（matplotlib 折線圖）。你會看到偶數 n 恆為 0、奇數 n 隨 n 增大趨近 0；用 `C_n` 的公式解釋這條曲線。

## 本章重點整理

- `(A^k)[s][t]` 是 s 到 t 的 k 步 walk 數；`trace(A^2) = 2|E|`、`trace(A^3) = 6 × 三角形數`。
- 實對稱矩陣有實特徵值與正交歸一特徵基；Rayleigh 商是特徵值的加權平均，猜測試向量就能得到 λ_1 的下界。
- `max(d_avg, √Δ) ≤ λ_1 ≤ Δ`，正則圖三者相等。
- 譜對 0 對稱 ⇔ 二分圖；證明一個方向靠翻號，另一個方向靠奇數次 trace 數閉 walk。
- 譜不是完整的同構不變量，鄰接譜甚至看不出連通性。

## 自我檢核

- [ ] 不看筆記，用「加權平均」這句話向同事解釋為什麼 `R(1) = d_avg` 推得出 `λ_1 ≥ d_avg`。
- [ ] 如果有人說「我用 `trace(A^4)/8` 數出了 4-cycle 數」，你能當場給一個讓他算錯的最小例子嗎？
- [ ] 能從零推出 `C_n` 的特徵值，並解釋為什麼大部分特徵值是二重的。
- [ ] 能說出 `eigvalsh` 用在有向圖上時，它實際上算的是哪一個矩陣。
- [ ] 想一個你工作中的網路（服務依賴圖、AD 圖、社交圖）：它的 λ_1 會比平均度數大很多還是差不多？為什麼？

## 延伸閱讀

### 書籍

- **[Spectral and Algebraic Graph Theory](https://www.cs.yale.edu/homes/spielman/sagt/)** — Daniel A. Spielman（Yale，未完成書稿，本課參照 2025-04-02 版）
  - **這本書的定位**：Part 5 的主參考；作者是譜圖論在演算法上應用的代表人物，寫法偏 CS，證明完整。書稿持續修改，章號可能變動，以下依 2025-04-02 版。
  - **讀哪幾章**：Chapter 2「Eigenvalues and Optimization: The Courant-Fischer Theorem」（本章 Rayleigh 商的完整版）、Chapter 4「Adjacency matrices, Eigenvalue Interlacing, and the Perron-Frobenius Theorem」（λ_1 的界、交錯定理、Wilf 定理、Perron–Frobenius）、Chapter 5「Fundamental Graphs」（完全圖、星、超立方體、圈、路徑的譜推導）、Chapter 19「Independent Sets and Coloring」（Hoffman 界）。
  - **前提知識**：本章的線代補課就足夠。
- **[Spectra of Graphs](https://www.win.tue.nl/~aeb/2WF05/spectra.pdf)** — Andries E. Brouwer、Willem H. Haemers（Springer Universitext，2012；連結是作者網站上的書稿 PDF）
  - **這本書的定位**：譜圖論的百科型參考，比 Spielman 更偏代數組合。
  - **讀哪幾章**：Chapter 1「Graph spectrum」（鄰接、Laplacian 等矩陣的定義與常見圖族的譜，本章那張表的完整版）；Chapter 3–4（最大、第二大、最小特徵值與交錯的應用）。
  - **前提知識**：線性代數熟練；部分章節需要群論。

### 論文

- **The eigenvalues of a graph and its chromatic number** — Herbert S. Wilf，Journal of the London Mathematical Society（1967）
  - **核心貢獻**：證明 `χ ≤ 1 + λ_1`，把 Brooks 定理的組合界換成譜界。
  - **讀哪裡**：全文很短，主定理的證明是對特徵值做歸納加上交錯定理。
  - **和本章的關聯**：本章「進階」一節表格裡的 Wilf 欄位。
- **On eigenvalues and colorings of graphs** — Alan J. Hoffman，收錄於 *Graph Theory and its Applications*（B. Harris 編，Academic Press，1970）
  - **核心貢獻**：給出 `χ ≥ 1 − λ_1/λ_n`，最早的譜著色下界之一。
  - **讀哪裡**：主定理與其證明；用到的就是本章的 Rayleigh 商與分塊矩陣。
  - **和本章的關聯**：Hoffman 欄位；Ch 30 的 expander 獨立集界是同一手法。

有了鄰接矩陣，下一章換成 Laplacian：它的二次型直接量測「切開一張圖要付出多少邊」，連通分量、生成樹數、最佳切割都會從它的特徵值裡讀出來。

→ [Ch 28 Laplacian、矩陣樹定理與 Cheeger 不等式](./28-laplacian-cheeger.md)
