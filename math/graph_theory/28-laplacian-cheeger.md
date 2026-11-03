# Ch 28 — Laplacian、矩陣樹定理與 Cheeger 不等式

> **目標**：理解 Laplacian 的二次型為什麼是「切開一張圖的代價」，並親手證明三件事：零特徵值的重數等於連通分量數、矩陣樹定理（生成樹數 = 任一餘子式）、Cheeger 不等式的容易方向；最後用 Fiedler 向量把一張圖切成兩半，並在小圖上暴力驗證每一個不等式。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、SciPy 1.18.1，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

這是原理深挖章：證明會寫完整，唯一借用而不證的是 Cauchy–Binet 公式與 Cheeger 不等式的困難方向，兩處都會明說。

## 為什麼需要這個？

Ch 27 的鄰接矩陣有個尷尬：它的譜看不出連通性（`K_{1,4}` 與 `C4 + K1` 同譜）。而在系統工作裡，我們最常問的圖問題恰好是連通性的「程度」：

- 這個服務網格切掉哪幾條鏈路會一分為二？
- 一個大型稀疏矩陣（有限元素、電路模擬）要怎麼切給 64 台機器，讓跨機通訊最少？
- 一張影像要怎麼分成前景與背景，讓「切斷」的相似度最少？

最小割（Ch 20）能精確回答「切成兩半最少要斷幾條邊」，但它偏愛切下一個孤零零的小點——切掉一個度數 1 的葉子永遠最便宜。我們真正想要的是**平衡**的切割，而「最小平衡割」是 NP-hard 的。

Laplacian 的歷史比圖論本身還早。1847 年 Kirchhoff 研究電路時，寫下了節點電壓的線性方程組，係數矩陣就是今天的 Laplacian，並且證明了它的餘子式在數生成樹。一百多年後，Fiedler（1973）把第二小特徵值命名為**代數連通度**（algebraic connectivity），指出它的特徵向量會把圖「拉開」。1980 年代 Alon–Milman 與 Alon 證明了離散版的 Cheeger 不等式，把 λ_2 和「最佳平衡切割」的品質綁在一起——從此譜分割（spectral partitioning）有了理論保證，2000 年 Shi–Malik 把它用到影像分割，變成今天譜分群（練習 E）的標準做法。

## 先建立直覺

把每條邊想成一根彈簧，每個點有一個位置 `x_v`（一維的，想成點被釘在數線上）。整個系統的彈性位能是

```text
E(x) = Σ_{uv ∈ E} (x_u − x_v)^2
```

這就是 Laplacian 的二次型 `x^T L x`。三個直覺馬上浮現：

```text
  全部點放在同一個位置          兩群各自聚在一起，                 隨便亂放
  ───────●───────               只有少數彈簧被拉長                  彈簧到處被拉長
  E = 0（常數向量）              ●●●●  ←—拉長—→  ●●●●                 E 很大
                                 E = 跨群的邊數 × 距離²
```

- 常數向量能量為 0，所以 0 永遠是特徵值。
- 如果圖本來就斷成兩塊，可以把兩塊放在不同位置而能量仍為 0——第二個零特徵值。
- 如果圖「幾乎」斷成兩塊（中間只連幾條邊），就能找到一個非常數的擺法，能量很小。這個最小的非零能量就是 λ_2，對應的擺法（Fiedler 向量）告訴你那兩塊在哪裡。

**這個類比的限制**：真實的彈簧系統要考慮質量才有振動頻率，那對應到正規化 Laplacian（每個點的質量 = 度數）。度數差異很大時，用 `L = D − A` 會被高度數點主導，這正是後面要引入正規化版本的原因。

> 二次型、Rayleigh 商與譜定理在 [Ch 27 — 譜圖論入門](./27-spectral-graph-theory.md) 的「線代補課」一節；本章直接使用。

## Laplacian 的定義與二次型

對簡單無向圖，`D` 是度數對角矩陣，`A` 是鄰接矩陣，**Laplacian** 定義為 `L = D − A`：

```text
L[u][u] = deg(u)
L[u][v] = −1    若 uv 是邊
L[u][v] = 0     其他
```

每一列加總為 0，所以 `L 1 = 0`。

另一個等價的寫法透過**有向關聯矩陣**（oriented incidence matrix）`B`：n 列 m 行，對每條邊 `e = uv` 任意選一個方向，在第 e 行放 `B[u][e] = +1`、`B[v][e] = −1`。則

```text
L = B B^T
```

驗證：`(B B^T)[u][u] = Σ_e B[u][e]^2 = deg(u)`；`u ≠ v` 時只有邊 uv 那一行兩者都非零，乘積為 `(+1)(−1) = −1`。方向怎麼選都一樣，因為乘積的正負號會抵消。

**定理 28.1（二次型）**：對任意實向量 x，`x^T L x = Σ_{uv∈E} (x_u − x_v)^2`。因此 L 是半正定的（positive semidefinite），所有特徵值 `0 = λ_1 ≤ λ_2 ≤ ... ≤ λ_n`。

**證明**：`x^T L x = x^T B B^T x = ‖B^T x‖^2`，而 `(B^T x)_e = x_u − x_v`。平方和非負，所以 Rayleigh 商非負，由 Ch 27 定理 27.3 所有特徵值 ≥ 0；又 `L 1 = 0`，最小特徵值是 0。∎

**注意排序方向**：鄰接矩陣習慣由大到小排（λ_1 最大），Laplacian 習慣由小到大排（λ_1 = 0、λ_2 是第二小）。這是兩個社群的慣例，兩邊都有人反著寫，讀論文時先確認。

```python
import networkx as nx
import numpy as np

rng = np.random.default_rng(0)


def laplacian(adj):
    nodes = sorted(adj)
    idx = {v: i for i, v in enumerate(nodes)}
    L = np.zeros((len(nodes), len(nodes)))
    for u in adj:
        for v in adj[u]:
            L[idx[u], idx[v]] = -1
        L[idx[u], idx[u]] = len(adj[u])
    return L


# 三個連通分量：三角形、4-path、孤立點
G = nx.disjoint_union_all([nx.complete_graph(3), nx.path_graph(4), nx.empty_graph(1)])
adj = {u: set(G[u]) for u in G}
L = laplacian(adj)

# 二次型 x^T L x = sum over edges (x_u - x_v)^2
for trial in range(3):
    x = rng.normal(size=len(adj))
    quad = x @ L @ x
    edge_sum = sum((x[u] - x[v]) ** 2 for u, v in G.edges())
    print(f"trial {trial}: x^T L x = {quad:.6f}   sum (x_u-x_v)^2 = {edge_sum:.6f}")

lam = np.linalg.eigvalsh(L)
print("eigenvalues:", np.round(lam, 4))
print("#zero eigenvalues =", int(np.sum(np.abs(lam) < 1e-9)),
      "| #components =", nx.number_connected_components(G))
print("networkx laplacian equal?", np.allclose(L, nx.laplacian_matrix(G, nodelist=sorted(G)).toarray()))
```

輸出（seed = 0）：

```text
trial 0: x^T L x = 3.031726   sum (x_u-x_v)^2 = 3.031726
trial 1: x^T L x = 11.825180   sum (x_u-x_v)^2 = 11.825180
trial 2: x^T L x = 9.229581   sum (x_u-x_v)^2 = 9.229581
eigenvalues: [-0.      0.      0.      0.5858  2.      3.      3.      3.4142]
#zero eigenvalues = 3 | #components = 3
networkx laplacian equal? True
```

`-0.` 是浮點運算得到的 `-1e-16` 之類的值，四捨五入後保留了負號。判斷「是不是零」一律用容差（這裡 `1e-9`），不要用 `== 0`。

## 定理：零特徵值的重數 = 連通分量數

**定理 28.2**：L 的特徵值 0 的重數，等於 G 的連通分量數 c。

**證明**：令 `N = {x : Lx = 0}` 為零空間。對實對稱矩陣，特徵值 0 的重數等於零空間的維度（譜定理給出正交特徵基，重數 = 屬於該特徵值的基向量數 = 該特徵空間維度）。所以只要證 `dim N = c`。

- **N 裡的向量在每個分量上是常數。** 若 `Lx = 0`，則 `0 = x^T L x = Σ_{uv∈E} (x_u − x_v)^2`。平方和為 0，所以每條邊 uv 都有 `x_u = x_v`。沿著路徑傳遞，同一個連通分量裡的所有點值相同。
- **每個分量的指示向量都在 N 裡。** 令 `C_1, ..., C_c` 為連通分量，`χ_i` 是 `C_i` 的指示向量（在 `C_i` 上為 1、其他為 0）。對 `u ∈ C_i`，`(L χ_i)_u = deg(u) · 1 − Σ_{w~u} χ_i(w) = deg(u) − deg(u) = 0`，因為 u 的鄰居全在 `C_i` 裡；對 `u ∉ C_i`，u 沒有鄰居在 `C_i` 裡，`(L χ_i)_u = 0 − 0 = 0`。
- **合起來。** 第一點說 N 中每個向量都是 `χ_1, ..., χ_c` 的線性組合；第二點說這些指示向量都在 N 裡；它們的支撐集互不相交，所以線性獨立。`dim N = c`。∎

推論：**G 連通 ⇔ λ_2 > 0**。這就是「代數連通度」這個名字的由來。

不要只信證明。`nx.graph_atlas_g()` 收錄了所有 0 到 7 個點的圖（同構類各一張，共 1253 張）；去掉 0 個點的那張，剩下 1252 張全部檢查一次，順便把下一節的矩陣樹定理也驗了：

```python
from itertools import combinations

import networkx as nx
import numpy as np


def count_spanning_trees_brute(G):
    """列舉所有 n-1 條邊的子集，數出無環且連通的那些。"""
    n, edges = G.number_of_nodes(), list(G.edges())
    if n == 1:
        return 1
    count = 0
    for sub in combinations(edges, n - 1):
        parent = {v: v for v in G}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        ok = True
        for u, v in sub:
            ru, rv = find(u), find(v)
            if ru == rv:            # 這條邊會形成環
                ok = False
                break
            parent[ru] = rv
        count += ok                 # n-1 條邊且無環 => 連通 => 生成樹
    return count


def matrix_tree(G):
    L = nx.laplacian_matrix(G, nodelist=sorted(G)).toarray().astype(float)
    return round(np.linalg.det(L[1:, 1:]))        # 刪掉第 0 列第 0 行


atlas = nx.graph_atlas_g()[1:]                   # 1252 張 1..7 點的圖（去掉 0 個點的那張）
zero_ok = tree_ok = 0
for G in atlas:
    lam = np.linalg.eigvalsh(nx.laplacian_matrix(G).toarray().astype(float))
    zero_ok += int(np.sum(np.abs(lam) < 1e-9)) == nx.number_connected_components(G)
    tree_ok += matrix_tree(G) == count_spanning_trees_brute(G)
print(f"graphs checked: {len(atlas)}")
print(f"#zero eigenvalues == #components : {zero_ok}/{len(atlas)}")
print(f"matrix-tree == brute force       : {tree_ok}/{len(atlas)}")

for n in range(2, 9):
    K = nx.complete_graph(n)
    print(f"K{n}: matrix-tree = {matrix_tree(K):>7d}   n^(n-2) = {n ** (n - 2):>7d}"
          f"   nx = {round(nx.number_of_spanning_trees(K)):>7d}")
```

輸出（在本機約 4 秒）：

```text
graphs checked: 1252
#zero eigenvalues == #components : 1252/1252
matrix-tree == brute force       : 1252/1252
K2: matrix-tree =       1   n^(n-2) =       1   nx =       1
K3: matrix-tree =       3   n^(n-2) =       3   nx =       3
K4: matrix-tree =      16   n^(n-2) =      16   nx =      16
K5: matrix-tree =     125   n^(n-2) =     125   nx =     125
K6: matrix-tree =    1296   n^(n-2) =    1296   nx =    1296
K7: matrix-tree =   16807   n^(n-2) =   16807   nx =   16807
K8: matrix-tree =  262144   n^(n-2) =  262144   nx =  262144
```

暴力法裡「n−1 條邊且無環 ⇒ 生成樹」用的是 Ch 9 樹的等價定義之一。最後一欄 `nx.number_of_spanning_trees` 在 NetworkX 3.7 原始碼裡也是 `float(np.linalg.det(L[1:, 1:]))`——同一個餘子式，所以和我們的 `matrix_tree` 一致不算獨立驗證；獨立的驗證是中間那欄 Cayley 公式 `n^(n−2)`。

## 矩陣樹定理

**定理 28.3（Kirchhoff 矩陣樹定理）**：令 `L_0` 為 L 刪掉第 i 列與第 i 行後的 (n−1)×(n−1) 矩陣（任選一個 i）。則 G 的生成樹數 `τ(G) = det(L_0)`。等價地，`τ(G) = (1/n) λ_2 λ_3 ⋯ λ_n`。

套到完全圖：`L(K_n) = nI − J`，特徵值是 0（向量 1）與 n（重數 n−1，任何 ⟂ 1 的向量），所以 `τ(K_n) = (1/n) n^{n−1} = n^{n−2}`。這就是 Ch 9 用 Prüfer 編碼證明的 **Cayley 公式**——兩條完全不同的路到達同一個數。

> Cayley 公式的組合證明（Prüfer 雙射）在 [Ch 9 — 樹：等價定義、Cayley 公式、Prüfer 編碼](./09-trees-cayley-prufer.md)。

### 證明

不失一般性取 i = 0（刪掉點 0 那一列那一行）。令 `B_0` 為關聯矩陣 B 刪掉第 0 列，大小 (n−1)×m。因為 `L = B B^T`，刪掉第 0 列第 0 行恰好得到 `L_0 = B_0 B_0^T`。

**借用的工具：Cauchy–Binet 公式**（本章不證，任何線性代數教科書都有）：若 P 是 k×m、Q 是 m×k，且 k ≤ m，則

```text
det(P Q) = Σ_{S ⊆ {1..m}, |S| = k} det(P[:, S]) · det(Q[S, :])
```

套用在 `P = B_0`、`Q = B_0^T`、k = n−1：

```text
det(L_0) = Σ_{|S| = n−1} det(B_0[:, S])^2
```

求和跑遍所有「n−1 條邊的子集 S」。所以只要證下面的引理，定理就完成：每個 S 貢獻 1 若 S 是生成樹、否則貢獻 0。

**引理 28.4**：對 n−1 條邊的子集 S，`det(B_0[:, S]) = ±1` 若 S 構成生成樹，否則為 0。

**證明**：

- **S 不是生成樹。** n 個點、n−1 條邊、又不是樹，那麼 S 不連通（n−1 條邊的連通圖必是樹，Ch 9）。取一個**不含點 0** 的連通分量 C（不連通至少有兩個分量，至多一個含點 0）。把 `B_0[:, S]` 中屬於 C 的那些列加起來：每條 S 中的邊，要嘛兩端都在 C（貢獻 +1 與 −1，抵消），要嘛兩端都不在 C（貢獻 0）——因為 C 是分量，沒有邊一端在 C 一端在外。所以這些列的和是零向量，列線性相依，行列式為 0。
- **S 是生成樹。** 對 n 歸納。n = 2 時矩陣是 1×1 的 `±1`。n ≥ 3 時，樹至少有兩個葉子，其中至少一個不是點 0，叫它 v，它唯一的樹邊叫 e。在 `B_0[:, S]` 中，v 那一列只有 e 那一行是 ±1，其他都是 0。沿著這一列做餘因子展開，`det = ±det(M')`，其中 M' 是刪掉 v 列與 e 行的矩陣——正好是「刪掉葉子 v 之後的樹（n−1 個點）」的 `B_0`。由歸納假設 `det(M') = ±1`。∎

由引理，`det(L_0) = Σ_S [S 是生成樹] = τ(G)`。

**特徵值形式**：特徵多項式 `det(tI − L) = Π (t − λ_i)`。一次項係數一方面等於 `(−1)^{n−1} Σ_i det(L 刪第 i 列第 i 行)`（行列式展開的標準事實：t 的一次項來自選 n−1 個對角上的 `−L` 部分），由上面已證的部分每一項都是 τ(G)，合計 `(−1)^{n−1} n τ(G)`；另一方面因為 `λ_1 = 0`，`Π(t − λ_i) = t Π_{i≥2}(t − λ_i)`，一次項係數是 `(−1)^{n−1} λ_2 ⋯ λ_n`。比較得 `τ(G) = (1/n) λ_2 ⋯ λ_n`。∎

在 K4 上檢查證明用到的兩個事實：

```python
from itertools import combinations

import networkx as nx
import numpy as np

# 在 K4 上檢查 Cauchy–Binet 證明要用的兩個事實
G = nx.complete_graph(4)
nodes, edges = sorted(G), list(G.edges())
B = np.zeros((len(nodes), len(edges)))
for j, (u, v) in enumerate(edges):        # 任意定向：u 為 +1，v 為 -1
    B[u, j], B[v, j] = 1, -1
L = nx.laplacian_matrix(G, nodelist=nodes).toarray()
print("B B^T == L ?", np.allclose(B @ B.T, L))
B0 = B[1:, :]                             # 刪掉第 0 列（reduced incidence）
dets = {}
for cols in combinations(range(len(edges)), len(nodes) - 1):
    d = round(np.linalg.det(B0[:, cols]))
    is_tree = nx.is_tree(nx.Graph([edges[c] for c in cols]))
    dets.setdefault((is_tree, abs(d)), 0)
    dets[(is_tree, abs(d))] += 1
print("(is_tree, |det|) -> count:", dets)
```

輸出：

```text
B B^T == L ? True
(is_tree, |det|) -> count: {(True, 1): 16, (False, 0): 4}
```

K4 有 `C(6,3) = 20` 個三邊子集：16 個是生成樹（`4^2 = 16`），行列式絕對值 1；剩下 4 個是三角形，行列式 0。和引理完全一致。（`nx.Graph(邊串列)` 只包含被邊碰到的點，三角形只有 3 個點，`is_tree` 回傳 False，正確。）

### 失敗示範：浮點行列式數不了大樹

上面的 `round(np.linalg.det(...))` 在 n ≤ 8 沒問題，但生成樹數長得極快：`τ(K_30) = 30^28 ≈ 2.3 × 10^41`，而 float64 只有約 16 位有效數字。用 Bareiss 演算法（整數版高斯消去，每一步的除法都整除）算精確值來對照：

```python
import networkx as nx
import numpy as np


def bareiss_det(M):
    """整數矩陣的精確行列式（Bareiss 分數消去，所有中間值都是整數）。"""
    M = [row[:] for row in M]
    n, sign, prev = len(M), 1, 1
    for k in range(n - 1):
        if M[k][k] == 0:                       # 找非零 pivot 換列
            for r in range(k + 1, n):
                if M[r][k] != 0:
                    M[k], M[r], sign = M[r], M[k], -sign
                    break
            else:
                return 0
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                M[i][j] = (M[i][j] * M[k][k] - M[i][k] * M[k][j]) // prev
        prev = M[k][k]
    return sign * M[-1][-1]


for n in (10, 20, 30):
    L = nx.laplacian_matrix(nx.complete_graph(n)).toarray()
    minor = L[1:, 1:]
    exact = bareiss_det(minor.astype(int).tolist())
    flt = round(np.linalg.det(minor.astype(float)))
    print(f"K{n}: exact == n^(n-2)? {exact == n ** (n - 2)}   float-round == exact? {flt == exact}"
          f"   rel.err = {abs(flt - exact) / exact:.1e}")
```

輸出：

```text
K10: exact == n^(n-2)? True   float-round == exact? True   rel.err = 0.0e+00
K20: exact == n^(n-2)? True   float-round == exact? False   rel.err = 3.5e-15
K30: exact == n^(n-2)? True   float-round == exact? False   rel.err = 6.5e-15
```

相對誤差只有 `1e-15`，但整數值已經錯了——`10^24` 量級的數，`1e-15` 的相對誤差就是個位數到十位數的絕對誤差。生成樹「數」是精確整數問題，要用整數演算法；float 的 `slogdet` 適合拿來估「大約有 10 的幾次方棵」。

## Fiedler 向量與代數連通度

由 Ch 27 定理 27.3（改成由小到大的版本）：

```text
λ_2 = min { x^T L x / x^T x  :  x ≠ 0, x ⟂ 1 }
```

限制 `x ⟂ 1` 是為了排除常數向量（能量 0 的平凡解）；同時 `Σ x_v = 0` 逼使 x 有正有負。最佳的 x——**Fiedler 向量**——是「在必須有正有負的前提下，讓被拉長的彈簧總能量最小」的擺法。直覺上它會把圖中兩個緊密的群放在數線兩側，把少數跨群的邊拉長。依符號切開，就得到一個切割。

```python
import networkx as nx
import numpy as np

# 兩個 K6 用 2 條邊接起來：結構上明顯該切成兩半
G = nx.disjoint_union(nx.complete_graph(6), nx.complete_graph(6))   # 0..5 與 6..11
G.add_edges_from([(0, 6), (1, 7)])
nodes = sorted(G)
L = nx.laplacian_matrix(G, nodelist=nodes).toarray().astype(float)
lam, vecs = np.linalg.eigh(L)
fiedler = vecs[:, 1]
print("lambda_2 (algebraic connectivity) =", round(lam[1], 6))
print("nx.algebraic_connectivity          =", round(nx.algebraic_connectivity(G, seed=0), 6))
print("Fiedler vector:", np.round(fiedler, 3))
S = {v for v in nodes if fiedler[v] < 0}
T = set(nodes) - S
print("sign cut:", sorted(S), "|", sorted(T), " cut edges =", nx.cut_size(G, S, T))

# 邊界：路徑圖的 Fiedler 向量是一條單調的 cos 曲線
P = nx.path_graph(8)
lp, vp = np.linalg.eigh(nx.laplacian_matrix(P).toarray().astype(float))
print("P8 Fiedler:", np.round(vp[:, 1] * np.sign(vp[0, 1]), 3), " lambda_2 =", round(lp[1], 4),
      " 2-2cos(pi/8) =", round(2 - 2 * np.cos(np.pi / 8), 4))
```

輸出：

```text
lambda_2 (algebraic connectivity) = 0.535898
nx.algebraic_connectivity          = 0.535898
Fiedler vector: [ 0.23   0.23   0.314  0.314  0.314  0.314 -0.23  -0.23  -0.314 -0.314
 -0.314 -0.314]
sign cut: [6, 7, 8, 9, 10, 11] | [0, 1, 2, 3, 4, 5]  cut edges = 2
P8 Fiedler: [ 0.49   0.416  0.278  0.098 -0.098 -0.278 -0.416 -0.49 ]  lambda_2 = 0.1522  2-2cos(pi/8) = 0.1522
```

三個觀察：

1. 兩個 K6 被放在數線兩側；點 0、1、6、7（有跨群邊的點）的值 `±0.23` 比群內其他點 `±0.314` 更靠近中間——它們被跨群彈簧往對面拉。
2. 符號切割恰好切斷那 2 條橋邊，是最佳平衡切割。
3. 路徑 `P_8` 的 Fiedler 向量是一條從 0.49 單調降到 −0.49 的餘弦曲線，`λ_2 = 2 − 2cos(π/n)`。這是「沒有明顯群結構」時的樣子：向量平滑地從一端滑到另一端，符號切割把路徑從中間切開，也合理。

`nx.algebraic_connectivity` 預設用 `tracemin_pcg` 迭代法求 λ_2，適合大型稀疏圖；`seed` 參數控制初始隨機向量。我們用 `np.linalg.eigh` 做稠密計算來對拍。

**λ_2 量測的是什麼？** Fiedler 證明了對非完全圖，`λ_2 ≤ κ(G)`（點連通度，Ch 12）。所以 λ_2 小代表有瓶頸；但反方向不成立——λ_2 很小不代表點連通度很小（例如長的圈 `C_n`，連通度 2，λ_2 ≈ 4π²/n² → 0）。λ_2 量測的是比連通度更細的東西：**擴散的瓶頸**，下一節的 Cheeger 不等式把它講精確。

## Cheeger 不等式

先定義「好的平衡切割」。對點集 S，`E(S, S̄)` 是跨越 S 與其補集的邊。

- **邊擴張**（edge expansion）：`h(G) = min_{0 < |S| ≤ n/2} |E(S, S̄)| / |S|`。「每個被切出的點平均要斷幾條邊」，限制 `|S| ≤ n/2` 避免取整張圖。
- **conductance**：`vol(S) = Σ_{v∈S} deg(v)`，`φ(S) = |E(S, S̄)| / min(vol(S), vol(S̄))`，`φ(G) = min_S φ(S)`。把「點數」換成「度數總和」，度數不均時更公平。
- **正規化 Laplacian**：`N = I − D^{−1/2} A D^{−1/2}`，特徵值 `0 = ν_1 ≤ ν_2 ≤ ... ≤ ν_n ≤ 2`。

對 d-正則圖，`N = L / d`、`ν_i = λ_i / d`、`φ = h / d`，兩套定義只差一個 d 的倍數。

**定理 28.5（Cheeger 不等式，離散版）**：對連通圖 G，

```text
ν_2 / 2  ≤  φ(G)  ≤  √(2 ν_2)
```

對 d-正則圖，換成 `λ_2 / 2 ≤ h(G) ≤ √(2 d λ_2)`。

左邊叫**容易方向**：λ_2 大 ⇒ 沒有便宜的切割（圖是好的 expander，Ch 30）。右邊叫**困難方向**：λ_2 小 ⇒ 存在便宜的切割，而且可以從 Fiedler 向量找出來。

### 容易方向的證明（組合版，對任何圖成立）

我們證：對任何圖，`λ_2(L) ≤ 2 h(G)`。

取達到 h(G) 的集合 S，`s = |S| ≤ n/2`。構造測試向量

```text
x_v = 1 − s/n   若 v ∈ S
x_v = −s/n      若 v ∉ S
```

也就是 `x = χ_S − (s/n) 1`。

- **`x ⟂ 1`**：`Σ x_v = s(1 − s/n) + (n − s)(−s/n) = s − s = 0`。
- **分子**：每條邊的兩端若同側，差為 0；若跨越 S 與 S̄，差為 `(1 − s/n) − (−s/n) = 1`。所以 `x^T L x = |E(S, S̄)|`。
- **分母**：`x^T x = s(1 − s/n)^2 + (n − s)(s/n)^2 = s(n − s)/n`（展開化簡）。因為 `s ≤ n/2`，`n − s ≥ n/2`，所以 `x^T x ≥ s/2`。

由 Courant–Fischer，`λ_2 ≤ x^T L x / x^T x ≤ |E(S, S̄)| / (s/2) = 2 h(G)`。∎

整個證明只是「猜一個測試向量」——這就是 Ch 27 說的：Rayleigh 商的上界只要猜。正規化版本 `ν_2/2 ≤ φ(G)` 的證明完全同構，測試向量改成 `D^{1/2}` 加權、把「⟂ 1」換成「⟂ D^{1/2} 1」，留作動手練習。

### 困難方向：這裡只給證明架構

困難方向要從一個實數向量「捏」出一個離散的集合，技巧是 **sweep cut**：

```text
1. 取 ν_2 的特徵向量 y，令 z = D^{−1/2} y（把正規化拿掉）
2. 依 z_v 由小到大排序：v_(1), v_(2), ..., v_(n)
3. 對每個 i，看前綴 S_i = {v_(1), ..., v_(i)} 的 conductance
4. 回傳最好的那個前綴
```

證明的核心是一個機率論證：隨機選一個門檻 t（依適當分佈），令 `S_t = {v : z_v ≤ t}`，計算 `E[|E(S_t, S̄_t)|]` 與 `E[vol(S_t)]` 的比值，再用 Cauchy–Schwarz 把分子界在 `√(2 ν_2)` 倍的分母以內。存在某個 t 使比值不超過期望值的比，而 S_t 必是某個前綴。完整證明約兩頁，見延伸閱讀的 Spielman 書 Chapter 21。**本章沒有證明這個方向**，但下面會實測它。

### 在小圖上暴力驗證

n ≤ 12 時可以列舉全部 `2^12` 個子集，精確算出 h 與 φ：

```python
from itertools import combinations

import networkx as nx
import numpy as np


def brute_expansion(G):
    """h(G) = min |E(S, S^c)| / |S|, |S| <= n/2；phi(G) = min |E(S,S^c)| / min(vol S, vol S^c)。"""
    nodes, n = sorted(G), G.number_of_nodes()
    vol_all = 2 * G.number_of_edges()
    h = phi = float("inf")
    for k in range(1, n):
        for S in combinations(nodes, k):
            cut = nx.cut_size(G, S)
            if k <= n // 2:
                h = min(h, cut / k)
            volS = sum(d for _, d in G.degree(S))
            phi = min(phi, cut / min(volS, vol_all - volS))
    return h, phi


def sweep_conductance(G):
    """Cheeger 困難方向的構造性證明：沿著 D^{-1/2} v2 排序，試每個前綴。"""
    nodes = sorted(G)
    A = nx.to_numpy_array(G, nodelist=nodes)
    d = A.sum(axis=1)
    N = np.eye(len(nodes)) - A / np.sqrt(np.outer(d, d))
    nu, vec = np.linalg.eigh(N)
    order = np.argsort(vec[:, 1] / np.sqrt(d))
    vol_all, best = d.sum(), float("inf")
    for i in range(1, len(nodes)):
        S = [nodes[j] for j in order[:i]]
        volS = sum(d[j] for j in order[:i])
        best = min(best, nx.cut_size(G, S) / min(volS, vol_all - volS))
    return nu[1], best


cases = {
    "barbell K6-K6": nx.barbell_graph(6, 0),
    "C12": nx.cycle_graph(12),
    "Petersen": nx.petersen_graph(),
    "Q3": nx.hypercube_graph(3),
    "3-reg n=12 s=5": nx.random_regular_graph(3, 12, seed=5),
    "G(12,.4) s=11": nx.gnp_random_graph(12, 0.4, seed=11),
}
print(f"{'graph':17s} {'lam2/2':>7s} {'h':>6s} | {'nu2/2':>6s} {'phi':>6s} {'sweep':>6s} {'sqrt(2nu2)':>10s}")
for name, G in cases.items():
    G = nx.convert_node_labels_to_integers(G)
    assert nx.is_connected(G)
    lam2 = np.linalg.eigvalsh(nx.laplacian_matrix(G).toarray().astype(float))[1]
    h, phi = brute_expansion(G)
    nu2, sweep = sweep_conductance(G)
    assert lam2 / 2 <= h + 1e-9 and nu2 / 2 <= phi + 1e-9 <= sweep + 2e-9 <= np.sqrt(2 * nu2) + 3e-9
    print(f"{name:17s} {lam2 / 2:7.3f} {h:6.3f} | {nu2 / 2:6.3f} {phi:6.3f} {sweep:6.3f} {np.sqrt(2 * nu2):10.3f}")
```

輸出（本機約 1 秒）：

```text
graph              lam2/2      h |  nu2/2    phi  sweep sqrt(2nu2)
barbell K6-K6       0.129  0.167 |  0.025  0.032  0.032      0.318
C12                 0.134  0.333 |  0.067  0.167  0.167      0.518
Petersen            1.000  1.000 |  0.333  0.333  0.467      1.155
Q3                  1.000  1.000 |  0.333  0.333  0.500      1.155
3-reg n=12 s=5      0.459  0.667 |  0.153  0.222  0.222      0.783
G(12,.4) s=11       0.848  1.500 |  0.239  0.360  0.360      0.977
```

（`nx.barbell_graph(6, 0)` 是兩個 K6 以**一條**邊相連，和上一節手工建的「兩條邊」版本不同。）讀這張表：

- **容易方向可以是緊的**：Petersen 與 Q3 的 `λ_2/2 = h = 1`。Q3 切成上下兩個正方形，斷 4 條邊、每側 4 點，`h = 1`；而 `λ_2(Q3) = 2`。
- **困難方向的平方根是必要的**：C12 的 `ν_2/2 = 0.067`，`φ = 0.167`，差了 2.5 倍，n 越大差越多。圈的 λ_2 ≈ 4π²/n²，而 φ ≈ 2/n（切兩條邊、各半圈）——λ_2 是 φ 的平方量級。Cheeger 不等式兩端差一個平方根，不是證明技巧不夠，是圈這種圖真的達到它。
- **sweep cut 不保證最佳**：Petersen 的 sweep 只找到 0.467，而真正的 φ 是 0.333。原因是 Petersen 的 `ν_2` 有 5 重（高度對稱），`eigh` 回傳特徵空間裡任意一個向量，沿著它排序不一定揭示最佳切割。sweep 只保證 `≤ √(2ν_2) = 1.155`，它做到了。
- 斷言那一行把整條不等式鏈 `ν_2/2 ≤ φ ≤ sweep ≤ √(2ν_2)` 都檢查了，六張圖全過。

## 對比與取捨

| 矩陣 | 定義 | 特徵值範圍 | 最擅長 | 弱點 |
| --- | --- | --- | --- | --- |
| 鄰接 `A` | 0/1 | `[−Δ, Δ]` | walk 計數、正則圖分析 | 看不出連通性 |
| Laplacian `L = D − A` | 度數 − 鄰接 | `[0, 2Δ]` | 連通分量、生成樹數、邊擴張 h | 度數不均時被高度數點主導 |
| 正規化 `N = I − D^{−1/2} A D^{−1/2}` | 對稱正規化 | `[0, 2]` | conductance、隨機遊走（Ch 29）、譜分群 | 孤立點 `deg = 0` 時未定義 |
| 隨機遊走 `I − D^{−1} A` | 列正規化 | 同 N（相似矩陣） | 直接對應轉移機率 | 不對稱，不能用 `eigh` |

**為什麼不直接解最小平衡割？** 因為它是 NP-hard。譜方法把離散問題「放寬」（relax）成連續問題：原本 x 只能取 ±1（代表兩側），現在允許任何實數，只要 `x ⟂ 1`。放寬後的問題是特徵值問題，多項式時間可解；Cheeger 不等式保證放寬的損失不超過一個平方根。這和 Ch 26 的 LP 放寬是同一種思維：**把整數問題放寬成容易的連續問題，再證明放寬的誤差有界**。

**為什麼不用最小割（Ch 20）？** 最小割沒有平衡約束，會切掉葉子。Fiedler 向量的 `x ⟂ 1` 本身就是一種「軟平衡」：向量必須有正有負，而且 `x^T x` 在分母裡懲罰極不平衡的切割（容易方向證明裡的 `s(n−s)/n`）。

## 踩雷集錦

1. **拿錯特徵向量**：`np.linalg.eigh` 由小到大回傳，Fiedler 向量是 `vecs[:, 1]`，不是 `vecs[:, -1]`、也不是 `vecs[1]`（那是一列，不是一個特徵向量）。最後一個錯誤特別隱蔽：程式照跑，切出來的東西像是隨機的。
2. **在不連通的圖上找 Fiedler 向量**：圖不連通時 λ_2 = 0，「第二個」零特徵向量是零空間中任意一個與 1 正交的向量，可能把好幾個分量混在一起。先 `nx.connected_components` 分開處理，每個分量各自做譜分割。
3. **用 `== 0` 數零特徵值**：浮點會給 `-1.2e-16` 或 `3e-15`。用容差，並且知道容差的尺度要跟著矩陣大小與元素量級調整（近似不連通的圖會有非常小但真的不是零的 λ_2，例如兩個 K100 只連一條邊）。
4. **用 float 行列式數生成樹**：如上所示，K20 就錯了。`nx.number_of_spanning_trees` 回傳的也是 float 行列式，大圖上同樣不是精確整數。要精確值用整數演算法（Bareiss、`fractions.Fraction`、或 SymPy）；要量級用 `np.linalg.slogdet`。
5. **以為 sweep cut 找到最佳切割**：它只保證 `√(2ν_2)` 的品質。Petersen 這種高度對稱的圖，sweep 結果還依賴 LAPACK 在重根特徵空間裡挑了哪個向量。
6. **把 `L` 與 `N` 的 Cheeger 不等式混用**：`ν_2/2 ≤ φ ≤ √(2ν_2)` 是正規化版本；`λ_2/2 ≤ h` 對 L 永遠成立，但 `h ≤ √(2 d λ_2)` 這種上界在非正則圖要把 d 換成最大度數，界會變鬆。看論文時先確認作者用哪個矩陣。

## 進階：再往深一層

**有效電阻與隨機生成樹**：把每條邊當成 1 歐姆的電阻，u 與 v 之間的有效電阻 `R_eff(u,v) = (e_u − e_v)^T L^+ (e_u − e_v)`（`L^+` 是偽逆）。一個漂亮的事實：邊 uv 出現在**均勻隨機生成樹**中的機率恰好等於 `R_eff(u,v)`。這是矩陣樹定理的延伸，也是 Spielman–Srivastava 圖稀疏化（graph sparsification）的出發點：依有效電阻取樣邊，可以用 `O(n log n / ε^2)` 條邊近似任何圖的 Laplacian 二次型。

**多路分割**：要切成 k 塊，自然的推廣是取前 k 個特徵向量，把每個點映射到 `R^k`，再用 k-means 分群——這就是練習 E 的 Ng–Jordan–Weiss 式譜分群。理論上對應的是 Lee、Oveis Gharan、Trevisan 的高階 Cheeger 不等式：`ν_k` 小若且唯若圖可以切成 k 塊、每塊 conductance 都小（相差一個與 k 有關的多項式因子）。

**大圖上的計算**：稠密 `eigh` 是 `O(n^3)`，n = 10^4 就要數分鐘並吃掉 800 MB。實務上用 Lanczos / LOBPCG（`scipy.sparse.linalg.eigsh`、`lobpcg`）只求最小幾個特徵值，每次迭代只做稀疏矩陣乘向量；NetworkX 的 `tracemin_pcg` 也是這一類。更快的路線是 Laplacian 線性系統求解器（Spielman–Teng 的近線性時間解法），它把「解 `Lx = b`」做到 `Õ(m)`，進而加速所有基於電流的演算法。

## 動手練習

1. 證明正規化版本的容易方向 `ν_2/2 ≤ φ(G)`：測試向量取 `y = D^{1/2}(χ_S − (vol(S)/vol(V)) 1)`，仿照本章組合版的三個步驟。
2. 修改暴力驗證程式，把 `nx.graph_atlas_g()` 中所有 6 點與 7 點的**連通**圖都跑一遍 Cheeger 不等式鏈，記錄 `φ / (ν_2/2)` 的最大值與最小值，看看容易方向在哪些圖上是緊的。
3. 用 Bareiss 算 `τ(Q_5)`（5 維超立方體），再用特徵值公式 `(1/n) Π λ_i` 搭配 Ch 27 的超立方體譜（Laplacian 特徵值 `2k`，重數 `C(d,k)`）推出閉式，兩者對照。
4. 故意弄壞：把 `fiedler = vecs[:, 1]` 改成 `vecs[1]`，在兩個 K6 的圖上看符號切割變成什麼、切斷幾條邊。

## 本章重點整理

- `L = D − A = B B^T`，二次型 `x^T L x = Σ_{uv∈E} (x_u − x_v)^2` 是彈簧能量；L 半正定。
- 零特徵值重數 = 連通分量數；G 連通 ⇔ λ_2 > 0。
- 矩陣樹定理：生成樹數 = 任一餘子式 = `(1/n) Π_{i≥2} λ_i`；證明 = Cauchy–Binet + 「n−1 條邊的關聯子矩陣行列式是 ±1 或 0」。用在 K_n 就是 Cayley 公式。
- Cheeger：`ν_2/2 ≤ φ(G) ≤ √(2ν_2)`。容易方向是一個測試向量；困難方向是 sweep cut，平方根在圈上是必要的。
- 譜分割是「把 NP-hard 的離散切割放寬成特徵值問題」，損失由 Cheeger 不等式控制。

## 自我檢核

- [ ] 不看筆記，從 `x^T L x = 0` 出發，用自己的話證明零特徵值的重數等於連通分量數，並說出「重數 = 零空間維度」這一步為什麼需要對稱性。
- [ ] 如果面試官問「矩陣樹定理為什麼對」，你能在白板上講出 Cauchy–Binet 之後的那個引理，包括「不是樹時哪幾列相加為零」？
- [ ] 能解釋為什麼 C_n 的 λ_2 是 `Θ(1/n²)` 而 conductance 是 `Θ(1/n)`，以及這說明了 Cheeger 不等式的哪一側不能改進。
- [ ] 給你一張有 1 萬個點、度數從 1 到 5000 的服務依賴圖，你會用 L 還是 N 做譜分割？為什麼？
- [ ] 能說出 sweep cut 在什麼情況下會找不到最佳 conductance 切割。

## 延伸閱讀

### 書籍

- **[Spectral and Algebraic Graph Theory](https://www.cs.yale.edu/homes/spielman/sagt/)** — Daniel A. Spielman（Yale，未完成書稿，本課參照 2025-04-02 版）
  - **這本書的定位**：本章的主參考，證明風格和本章一致。
  - **讀哪幾章**：Chapter 3「The Laplacian and Graph Drawing」（用 Laplacian 特徵向量畫圖，把「彈簧」直覺變成圖片）；Section 13.4「The Matrix Tree Theorem」；Chapter 20「Graph Partitioning」（conductance 與正規化 Laplacian 的定義）；Chapter 21「Cheeger's Inequality」（困難方向的完整證明，就是本章只給架構的那部分）。
  - **前提知識**：Ch 27 與本章。

### 論文

- **[Algebraic connectivity of graphs](https://doi.org/10.21136/CMJ.1973.101168)** — Miroslav Fiedler，Czechoslovak Mathematical Journal 23(2)（1973），298–305
  - **核心貢獻**：把 λ_2 命名為代數連通度，證明它與點/邊連通度的不等式，以及它在加邊時單調不減。
  - **讀哪裡**：全文 8 頁，前半的性質列表可以直接對照本章；dml.cz 有免費掃描。
  - **和本章的關聯**：「Fiedler 向量」一節的名字與 `λ_2 ≤ κ(G)` 出自這裡。
- **[λ1, isoperimetric inequalities for graphs, and superconcentrators](https://www.tau.ac.il/~nogaa/PDFS/Publications2/lambda%20%20isoperimetric%20inequalities%20for%20graphs%20and%20superconcentrators.pdf)** — Noga Alon、Vitali D. Milman，Journal of Combinatorial Theory, Series B 38(1)（1985），73–88
  - **核心貢獻**：把黎曼幾何的 Cheeger 不等式搬到圖上，證明譜間隙與擴張性的雙向關係，並用來構造 superconcentrator。
  - **讀哪裡**：Section 2 的不等式陳述；後半的 superconcentrator 應用可跳過。連結是 Alon 個人網頁上的 PDF。
  - **和本章的關聯**：Cheeger 不等式在圖論上的源頭之一；另一篇是 Alon 1986 年的〈Eigenvalues and expanders〉（Ch 30 會列）。
- **[Normalized cuts and image segmentation](https://doi.org/10.1109/34.868688)** — Jianbo Shi、Jitendra Malik，IEEE TPAMI 22(8)（2000），888–905
  - **核心貢獻**：提出 normalized cut 準則（本質上是 conductance），證明它的放寬就是正規化 Laplacian 的第二特徵向量，用於影像分割。
  - **讀哪裡**：Section 2（從 Ncut 推到廣義特徵值問題）；實驗部分可跳過。
  - **和本章的關聯**：「為什麼要正規化」的工程答案；練習 E 的譜分群直接用它的想法。

### 歷史原典

- **[Ueber die Auflösung der Gleichungen, auf welche man bei der Untersuchung der linearen Vertheilung galvanischer Ströme geführt wird](https://sites.math.rutgers.edu/~zeilberg/akherim/kirchoff1847.pdf)** — G. Kirchhoff，Annalen der Physik und Chemie 72（1847），497–508
  - **這篇說什麼**：Kirchhoff 解電路方程時發現解可以寫成「生成樹的和」，即矩陣樹定理的原型。
  - **讀哪裡**：德文原文；能讀德文的話看前幾頁的方程組設定即可。不讀也沒關係，知道「Laplacian 起源於電路」就夠。
  - **為什麼值得列**：解釋了為什麼本章的「有效電阻」「電流」語言在圖論裡這麼自然。連結是 Rutgers 的 Zeilberger 網頁上的掃描。

Laplacian 是「彈簧」；把它換個角度看，正規化之後的 `D^{−1} A` 是一個機率矩陣——一個在圖上亂走的人。下一章從這個人出發，看他走多久會「忘記起點」，以及 Google 怎麼用他來排序網頁。

→ [Ch 29 隨機遊走與 PageRank](./29-random-walks-pagerank.md)
