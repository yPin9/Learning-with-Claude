# Ch 26 — LP 對偶與全單模矩陣

> **目標**：證明 LP 的弱對偶、互補鬆弛，以 Farkas 引理為前提推出強對偶；證明全單模（TU）矩陣的 LP 有整數最優頂點；然後用同一個框架重新得到 max-flow/min-cut、König、匈牙利法的對偶證書與 MST，全部用 `scipy.optimize.linprog` 實際解出來。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、SciPy 1.18.1，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

Part 4 到這裡，你已經看過一長串「最大 = 最小」的定理：

| 章 | 最大化的東西 | 最小化的東西 |
| --- | --- | --- |
| [Ch 12](./12-menger-theorem.md) | 不相交路徑數 | 分隔點/邊集大小 |
| [Ch 20](./20-max-flow-min-cut.md) | s-t 流量 | s-t 割容量 |
| [Ch 22](./22-bipartite-matching.md) | 二分圖 matching 大小 | vertex cover 大小 |
| [Ch 24](./24-min-cost-flow-assignment.md) | 對偶 `Σu + Σv` | 指派成本 |
| [Ch 25](./25-matroids.md) | 兩 matroid 的共同獨立集 | `r1(A) + r2(E \ A)` |

每一個都各自用組合論證證過一次。這麼多巧合，背後應該有同一個原因。那個原因就是**線性規劃對偶（LP duality）**：每個這類問題都能寫成一個 LP，它的對偶 LP 恰好是另一邊的問題；而它們的限制矩陣有一個叫**全單模（totally unimodular, TU）**的性質，保證 LP 的最優解自動是整數。

歷史上方向其實是反過來的。1947 年 Dantzig 提出 simplex；一般認為 LP 對偶的想法是同年 von Neumann 在和 Dantzig 的討論中提出的。1950 年代 Ford–Fulkerson、Kuhn、Hoffman 等人發現網路問題的 LP 有整數頂點，Hoffman 與 Kruskal（1956）把這件事歸結到 TU 矩陣。換句話說，Part 4 的很多組合定理，最早就是在 LP 的語言裡被理解的。

這章的實用價值是：碰到新的組合問題，先寫成 LP、寫出對偶、檢查限制矩陣是不是 TU。若是，你立刻得到一個 min-max 定理和一個多項式時間演算法（直接丟 LP solver）；若不是，你至少知道 LP 鬆弛的值是一個界。

## 先建立直覺

想像你經營一間小工廠，生產兩種產品，受機器時數、原料、市場上限三種資源限制，要最大化利潤——這是 **primal**。現在有個收購商想把你的三種資源全部買走，他要為每單位資源開價 `y_i`。為了讓你願意賣，每種產品用到的資源總價，必須不少於你自己生產它的利潤；收購商在這個條件下想付最少的錢——這是 **dual**。

```text
primal（工廠）                         dual（收購商）
max  3x1 + 2x2                         min  4y1 + 6y2 + 3y3
s.t. x1 +  x2 <= 4   機器    ── y1     s.t. y1 + y2 + y3 >= 3   產品 1 的利潤
     x1 + 2x2 <= 6   原料    ── y2          y1 + 2y2      >= 2   產品 2 的利潤
     x1       <= 3   市場    ── y3          y >= 0
     x >= 0
```

收購商的報價一定不低於你的最大利潤（否則你自己生產更賺）——這是**弱對偶**。神奇的是兩者在最優時**完全相等**——這是**強對偶**。而且，若某種資源你沒用完，它在最優報價裡價格是 0；若某產品你有生產，它的資源總價恰好等於利潤——這是**互補鬆弛**。

## 用 linprog 實際解一次

`linprog` 只做最小化，限制寫成 `A_ub x <= b_ub`。最大化就把目標取負號。

```python
"""一個兩變數 LP：primal、dual、弱對偶、互補鬆弛，全部用 linprog 實際解。"""
import numpy as np
from scipy.optimize import linprog

# primal:  max 3x1 + 2x2
#          x1 +  x2 <= 4      （機器時數）
#          x1 + 2x2 <= 6      （原料）
#          x1       <= 3      （市場上限）
#          x >= 0
c = np.array([3, 2])
A = np.array([[1, 1], [1, 2], [1, 0]])
b = np.array([4, 6, 3])

# linprog 只做 minimize，所以目標取負號
p = linprog(-c, A_ub=A, b_ub=b, bounds=[(0, None)] * 2, method="highs")
print("primal x* =", p.x, " value =", -p.fun)

# dual:  min 4y1 + 6y2 + 3y3   s.t.  A^T y >= c, y >= 0   →  寫成 -A^T y <= -c
d = linprog(b, A_ub=-A.T, b_ub=-c, bounds=[(0, None)] * 3, method="highs")
print("dual   y* =", d.x, " value =", d.fun)

# HiGHS 也直接回傳對偶值：marginals = d(目標)/d(b)；我們最小化的是 -c·x，所以取負號
print("primal 的 ineqlin.marginals =", p.ineqlin.marginals, "→ 取負號 =", -p.ineqlin.marginals)

# 弱對偶：任意可行 x、y 都有 c·x <= b·y
rng = np.random.default_rng(26)
worst_gap = min(b @ y - c @ x
                for x in rng.uniform(0, 3, size=(2000, 2)) if np.all(A @ x <= b)
                for y in [rng.uniform(0, 3, size=3)] if np.all(A.T @ y >= c))
print("min (b·y - c·x) over random feasible pairs =", round(worst_gap, 4))

# 互補鬆弛：y_i > 0 → 第 i 條限制是緊的；x_j > 0 → 第 j 條對偶限制是緊的
slack = b - A @ p.x
reduced = A.T @ d.x - c
print("primal slack  =", slack, "  y* * slack =", d.x * slack)
print("dual surplus  =", reduced, "  x* * surplus =", p.x * reduced)

# 退化（degenerate）：把原料限制改成 x1 + 3x2 <= 6，三條線交在同一點 (3, 1)
A2 = np.array([[1, 1], [1, 3], [1, 0]])
p2 = linprog(-c, A_ub=A2, b_ub=b, bounds=[(0, None)] * 2, method="highs")
d2 = linprog(b, A_ub=-A2.T, b_ub=-c, bounds=[(0, None)] * 3, method="highs")
print("degenerate: x* =", p2.x, " dual LP y* =", d2.x, " -marginals =", -p2.ineqlin.marginals)
print("both dual values:", b @ d2.x, b @ -p2.ineqlin.marginals)
```

```text
primal x* = [3. 1.]  value = 11.0
dual   y* = [2. 0. 1.]  value = 11.0
primal 的 ineqlin.marginals = [-2. -0. -1.] → 取負號 = [2. 0. 1.]
min (b·y - c·x) over random feasible pairs = 1.0693
primal slack  = [0. 1. 0.]   y* * slack = [0. 0. 0.]
dual surplus  = [0. 0.]   x* * surplus = [0. 0.]
degenerate: x* = [3. 1.]  dual LP y* = [2. 0. 1.]  -marginals = [0.         0.66666667 2.33333333]
both dual values: 11.0 11.0
```

逐行讀：

- primal 和 dual 的最優值都是 11——強對偶。
- HiGHS 在 `p.ineqlin.marginals` 直接回傳對偶值。它的定義是「目標函數對右手邊 b 的導數」；因為我們最小化的是 `−c·x`，取負號才是 dual 的 y，正好等於另外解 dual LP 得到的 `[2, 0, 1]`。
- 弱對偶的隨機檢查：在隨機抽到的可行 (x, y) 配對裡，`b·y − c·x` 最小是 1.07 > 0，沒有任何一對違反。
- 原料限制（第 2 條）有 1 單位的 slack，它的對偶價格 `y2 = 0`；x1、x2 都 > 0，兩條對偶限制都是緊的（surplus 0）——互補鬆弛。
- 最後兩行是**故意弄壞**的版本：原料限制改成 `x1 + 3x2 <= 6` 後，三條限制線剛好交在 (3, 1)，primal 最優點是**退化的（degenerate）**。這時對偶最優解不唯一：解 dual LP 得到 `[2, 0, 1]`，從 primal 拿 marginals 卻是 `[0, 2/3, 7/3]`，兩者的值都是 11。如果你把 marginals 當成「資源的唯一影子價格」去做決策，會被這種情況騙。

## 底層機制：三個對偶定理

標準形式：

```text
(P)  max c·x   s.t. Ax <= b, x >= 0
(D)  min b·y   s.t. Aᵀy >= c, y >= 0
```

**定理 1（弱對偶）**：若 x 對 (P) 可行、y 對 (D) 可行，則 `c·x ≤ b·y`。

證明。`c·x ≤ (Aᵀy)·x = yᵀ(Ax) ≤ y·b`。第一個不等號用 `Aᵀy ≥ c` 與 `x ≥ 0`（逐項相乘不改方向）；第二個用 `Ax ≤ b` 與 `y ≥ 0`。∎

推論：若找到可行的 x、y 使 `c·x = b·y`，兩者同時最優。這就是「證書」的意思——Ch 24 匈牙利法的 `Σu + Σv = 成本` 正是這個推論。

**定理 2（互補鬆弛）**：可行的 x、y 同時最優，若且唯若對每個 i，`y_i·(b − Ax)_i = 0`，且對每個 j，`x_j·(Aᵀy − c)_j = 0`。

證明。把弱對偶的鏈寫成 `b·y − c·x = y·(b − Ax) + x·(Aᵀy − c)`（展開即得）。右邊兩個內積的每一項都是兩個非負數相乘。由強對偶（定理 3），x、y 同時最優 ⟺ 左邊為 0 ⟺ 右邊每一項都是 0。∎（「⇐」方向不需要強對偶：每項為 0 推出 `c·x = b·y`，由弱對偶推論兩者最優。）

**Farkas 引理**：下列兩者恰有一個成立：(a) 存在 `x ≥ 0` 使 `Ax ≤ b`；(b) 存在 `y ≥ 0` 使 `Aᵀy ≥ 0` 且 `b·y < 0`。

「不會同時成立」可以直接證：若兩者都成立，`0 ≤ (Aᵀy)·x = yᵀ(Ax) ≤ y·b < 0`，矛盾。「至少一個成立」需要凸集分離定理：若 (a) 不成立，b 不在閉凸錐 `{Ax + s : x, s ≥ 0}` 裡，分離超平面的法向量就是 (b) 要的 y。**這裡只給證明架構**，分離定理與「這個錐是閉的」需要一點實分析，完整證明見延伸閱讀的 Matoušek–Gärtner 第 6 章。

**定理 3（強對偶）**：若 (P) 有有限的最優值 `z*`，則 (D) 有可行解 y 使 `b·y = z*`。

證明（以 Farkas 引理為前提）。我們要證下列系統可行：`Aᵀy ≥ c`、`b·y ≤ z*`、`y ≥ 0`。寫成 `My ≤ r` 的形式，其中 `M = [−Aᵀ; bᵀ]`、`r = [−c; z*]`。若它不可行，由 Farkas 引理存在 `(x, λ) ≥ 0` 使 `Mᵀ(x, λ) ≥ 0` 且 `r·(x, λ) < 0`，也就是

```text
λb − Ax ≥ 0      且      λz* − c·x < 0
```

- 若 `λ > 0`：`x/λ` 滿足 `A(x/λ) ≤ b`、`x/λ ≥ 0`，是 (P) 的可行解，但 `c·(x/λ) > z*`，和 z* 是最優值矛盾。
- 若 `λ = 0`：`Ax ≤ 0`、`x ≥ 0`、`c·x > 0`。取 (P) 的最優解 `x*`，則 `x* + t·x` 對所有 `t ≥ 0` 都可行，目標值 `z* + t·(c·x)` 趨於無限，和 z* 有限矛盾。

所以系統可行，存在 y 對 (D) 可行且 `b·y ≤ z*`；由弱對偶 `b·y ≥ z*`，等號成立。∎

## 全單模：為什麼 LP 的解會自動是整數

組合問題要的是整數解（一條邊選或不選），LP 給的是實數。一般而言兩者差很多，但某些限制矩陣保證 LP 的頂點全是整數。

**定義**：矩陣 A 是**全單模（TU）**，若它的每一個方陣子矩陣的行列式都屬於 `{−1, 0, 1}`。（特別地，每個元素都是 −1、0 或 1。）

**定理 4（TU ⇒ 整數頂點）**：若 A 是 TU、b 是整數向量，則 `P = {x : Ax ≤ b, x ≥ 0}` 的每個頂點都是整數向量。

證明。把限制寫成 `A'x ≤ b'`，其中 `A' = [A; −I]`、`b' = [b; 0]`。`A'` 仍是 TU：它的方陣子矩陣若含 `−I` 的列，沿那些列做餘因子展開，每列只有一個 −1，行列式等於 ±（A 的某個子矩陣的行列式）或 0。LP 理論的基本事實是：x 是 P 的頂點，若且唯若它是某 n 條緊限制所組成、係數矩陣非奇異的方程組 `Bx = b_B` 的唯一解（B 是 `A'` 的 n×n 子矩陣）。B 非奇異且 TU，所以 `det B = ±1`。由 Cramer 法則 `x_j = det(B_j) / det(B)`，其中 `B_j` 是把 B 的第 j 行換成 `b_B`，是整數矩陣，行列式是整數。所以 x 是整數。∎

simplex 類演算法回傳的是頂點，所以 TU 的 LP 丟給 simplex 就直接得到整數最優解。這也是下面程式都用 `method="highs-ds"`（HiGHS 的 dual simplex）的原因：最優解若是一整個面，內點法可能回傳面中間的非整數點。

**定理 5（二分圖關聯矩陣是 TU）**：二分圖 (L ∪ R, E) 的點-邊關聯矩陣（`M[v][e] = 1` 若 v 是 e 的端點）是 TU。

證明。對 k×k 子矩陣 B 的大小 k 做歸納。k = 1 時元素是 0 或 1。設 k > 1：B 的每一行（對應一條邊）最多兩個 1。(i) 若某行全 0，`det B = 0`。(ii) 若某行恰一個 1，沿那一行展開，`det B = ±det(B')`，B' 是 (k−1)×(k−1) 子矩陣，由歸納屬於 `{−1,0,1}`。(iii) 否則每一行恰有兩個 1，而且因為圖是二分的，一個在 L 的列、一個在 R 的列。把 B 所有 L 列加總、減掉所有 R 列加總，每一行都得 `1 − 1 = 0`，列線性相依，`det B = 0`。∎

**定理 6（有向圖關聯矩陣是 TU）**：有向圖的關聯矩陣（`M[v][e] = −1` 若 e 從 v 出發、`+1` 若 e 進入 v）是 TU。

證明。同樣歸納，(i)(ii) 不變；(iii) 每一行恰有一個 +1 和一個 −1，所有列相加為 0，`det B = 0`。∎

注意 (iii) 在一般無向圖會失敗：奇圈的關聯矩陣行列式是 ±2。下面暴力驗證：

```python
"""全單模（TU）暴力檢查 + König 定理的 LP 版本 + 奇圈的整數性落差。"""
from itertools import combinations

import networkx as nx
import numpy as np
from scipy.optimize import linprog


def incidence(G, directed=False):
    nodes, edges = list(G.nodes), list(G.edges)
    M = np.zeros((len(nodes), len(edges)), dtype=int)
    for j, (u, v) in enumerate(edges):
        M[nodes.index(u), j] = -1 if directed else 1
        M[nodes.index(v), j] = 1
    return M


def is_tu(M):
    """暴力檢查每個方陣子矩陣的行列式是否 ∈ {-1, 0, 1}；回傳 (結果, 反例大小, 反例行列式)。"""
    r, c = M.shape
    for k in range(1, min(r, c) + 1):
        for rows in combinations(range(r), k):
            for cols in combinations(range(c), k):
                det = round(np.linalg.det(M[np.ix_(rows, cols)]))
                if det not in (-1, 0, 1):
                    return False, k, det
    return True, None, None


bip = nx.complete_bipartite_graph(2, 3)
c5 = nx.cycle_graph(5)
dig = nx.gnp_random_graph(5, 0.5, seed=26, directed=True)
print("K_{2,3} incidence     TU:", is_tu(incidence(bip)))
print("C_5 incidence         TU:", is_tu(incidence(c5)))
print("random digraph (5 nodes, %d arcs) incidence TU:" % dig.number_of_edges(),
      is_tu(incidence(dig, directed=True)))


def matching_and_cover_lp(G):
    M = incidence(G)
    n, m = M.shape
    # matching LP: max Σ x_e,  M x <= 1,  x >= 0
    mt = linprog(-np.ones(m), A_ub=M, b_ub=np.ones(n), bounds=[(0, None)] * m, method="highs-ds")
    # vertex cover LP（對偶）: min Σ y_v,  M^T y >= 1,  y >= 0
    vc = linprog(np.ones(n), A_ub=-M.T, b_ub=-np.ones(m), bounds=[(0, None)] * n, method="highs-ds")
    return -mt.fun, mt.x, vc.fun, vc.x


rng = np.random.default_rng(7)
ok = tested = 0
for trial in range(100):
    G = nx.bipartite.random_graph(rng.integers(2, 7), rng.integers(2, 7), 0.4, seed=trial)
    G.remove_nodes_from([v for v in list(G) if G.degree(v) == 0])
    if G.number_of_edges() == 0:
        continue
    tested += 1
    mlp, x, clp, y = matching_and_cover_lp(G)
    top = {v for v, d in G.nodes(data="bipartite") if d == 0}
    mm = len(nx.bipartite.hopcroft_karp_matching(G, top_nodes=top)) // 2
    integral = np.allclose(x, np.round(x)) and np.allclose(y, np.round(y))
    ok += (mlp == clp == mm) and integral
print("bipartite: LP matching == LP cover == Hopcroft-Karp, integral vertices:", ok, "/", tested)

mlp, x, clp, y = matching_and_cover_lp(c5)
print("C_5: LP matching =", mlp, "x =", x, "| LP cover =", clp, "y =", y)
print("C_5: integer matching =", len(nx.max_weight_matching(c5, maxcardinality=True)),
      "| integer cover =", min(k for k in range(6) for S in combinations(range(5), k)
                             if all(u in S or v in S for u, v in c5.edges)))
```

```text
K_{2,3} incidence     TU: (True, None, None)
C_5 incidence         TU: (False, 5, -2)
random digraph (5 nodes, 7 arcs) incidence TU: (True, None, None)
bipartite: LP matching == LP cover == Hopcroft-Karp, integral vertices: 98 / 98
C_5: LP matching = 2.5 x = [0.5 0.5 0.5 0.5 0.5] | LP cover = 2.5 y = [0.5 0.5 0.5 0.5 0.5]
C_5: integer matching = 2 | integer cover = 3
```

- `is_tu` 窮舉所有方陣子矩陣，`K_{2,3}` 與隨機有向圖都通過；`C_5` 的完整 5×5 關聯矩陣行列式是 −2。
- 98 個非空隨機二分圖上，matching LP 與 vertex cover LP 的最優值相等（強對偶），而且最優頂點是整數（定理 4、5），於是 LP 的值就是最大 matching 與最小 vertex cover——這就是 **König 定理的 LP 證明**：TU 給整數性，強對偶給相等。最後再和 `hopcroft_karp_matching` 對拍。
- `C_5` 的 LP 最優解是每條邊 0.5、每個點 0.5，值 2.5；整數版本卻是 matching 2、cover 3。LP 值夾在中間，兩邊都差 0.5——這叫**整數性落差（integrality gap）**。一般圖的 matching 要靠 Edmonds 的 blossom 不等式（每個奇數點集 S 內的邊最多 `(|S| − 1)/2` 條）才能讓 LP 頂點變回整數，這正是 [Ch 23](./23-general-matching-blossom.md) blossom 演算法的多面體版本。

## 把 max-flow 寫成 LP

變數是每條邊的流量 `f_e`。守恆限制的係數矩陣就是有向圖關聯矩陣（只取內部點的列），容量是變數的上界。它的對偶有兩組變數：每條邊的 `d_e ≥ 0`（這條邊「被切了多少」），每個點的 `p_v`（點的「位勢」，固定 `p_s = 1`、`p_t = 0`），限制 `d_uv ≥ p_u − p_v`。

```python
"""max-flow 的 LP 與它的對偶（min-cut LP），用 linprog 解，和 NetworkX 對答案。"""
import networkx as nx
import numpy as np
from scipy.optimize import linprog

cap = {("s", "a"): 3, ("s", "b"): 2, ("a", "b"): 1, ("a", "c"): 3,
       ("b", "d"): 2, ("c", "t"): 2, ("d", "t"): 3, ("c", "d"): 1}
E = list(cap)
V = sorted({x for e in E for x in e})
inner = [v for v in V if v not in ("s", "t")]
m = len(E)

# ---- primal：變數 f_e。max Σ_{e 離開 s} f_e
#      守恆：對每個內部點 v，流入 - 流出 = 0；容量：0 <= f_e <= c_e
N = np.array([[(e[1] == v) - (e[0] == v) for e in E] for v in inner])  # 有向關聯矩陣（內部點的列）
obj = np.array([1.0 if e[0] == "s" else 0.0 for e in E])
P = linprog(-obj, A_eq=N, b_eq=np.zeros(len(inner)),
            bounds=[(0, cap[e]) for e in E], method="highs")
print("LP max-flow value =", -P.fun)
print("flow =", {f"{u}{v}": float(x) for (u, v), x in zip(E, P.x)})

# ---- dual：變數 d_e >= 0（邊被切的程度）、p_v（點的「電位」，p_s = 1、p_t = 0）
#      min Σ c_e d_e   s.t.  d_uv >= p_u - p_v  對每條邊
pv = {v: i for i, v in enumerate(V)}                 # 變數排列：[d_e ..., p_v ...]
A_ub, b_ub = [], []
for i, (u, v) in enumerate(E):
    row = np.zeros(m + len(V))
    row[i] = -1                                       # -d_uv + p_u - p_v <= 0
    row[m + pv[u]] += 1
    row[m + pv[v]] -= 1
    A_ub.append(row)
    b_ub.append(0)
A_eq = np.zeros((2, m + len(V)))
A_eq[0, m + pv["s"]] = 1
A_eq[1, m + pv["t"]] = 1
D = linprog(np.r_[[cap[e] for e in E], np.zeros(len(V))], A_ub=np.array(A_ub), b_ub=b_ub,
            A_eq=A_eq, b_eq=[1, 0],
            bounds=[(0, None)] * m + [(None, None)] * len(V), method="highs-ds")
cut = [f"{u}{v}" for (u, v), x in zip(E, D.x[:m]) if x > 0.5]
print("LP min-cut value  =", D.fun, " cut edges =", cut,
      " d values =", sorted({float(abs(x)) for x in np.round(D.x[:m], 6)}))

G = nx.DiGraph()
for (u, v), c in cap.items():
    G.add_edge(u, v, capacity=c)
val, (S, T) = nx.minimum_cut(G, "s", "t")
print("nx  min-cut value =", val, " S side =", sorted(S))
```

```text
LP max-flow value = 5.0
flow = {'sa': 3.0, 'sb': 2.0, 'ab': 0.0, 'ac': 3.0, 'bd': 2.0, 'ct': 2.0, 'dt': 3.0, 'cd': 1.0}
LP min-cut value  = 5.0  cut edges = ['ct', 'dt']  d values = [0.0, 1.0]
nx  min-cut value = 5  S side = ['a', 'b', 'c', 'd', 's']
```

對偶 LP 的最優解裡，`d` 只取 0 和 1，被切的邊 `{c→t, d→t}` 容量 2 + 3 = 5，和 NetworkX 的 min cut 一致（NetworkX 回傳的 S 側是 `{s, a, b, c, d}`，切的正是這兩條邊）。這裡有兩件事值得想清楚：

**為什麼對偶解是一個割**：對偶的限制矩陣是 `[−I | Nᵀ]` 再加兩條固定 `p_s`、`p_t` 的單位列，Nᵀ 是有向關聯矩陣的轉置（TU 的轉置仍是 TU），補上單位矩陣的行仍是 TU，所以 dual simplex 回傳整數頂點。給定整數最優解 (d, p)，令 `S = {v : p_v ≥ 1}`，則 s ∈ S、t ∉ S；對每條從 S 指向外面的邊，`p_u − p_v ≥ 1`（整數），所以 `d_uv ≥ 1`。割 (S, V \ S) 的容量 ≤ `Σ c_e d_e` = LP 值。

**這是 max-flow min-cut 的第二個證明**：min cut ≤ 對偶 LP 值（上一段）= primal LP 值（強對偶）= 最大流（primal 的頂點是整數流，定理 4 + 6）≤ min cut（任何流不超過任何割，Ch 20 的弱方向）。四個不等號夾起來全部相等。和 Ch 20 的增廣路證明相比，這個證明不構造演算法，但它把「為什麼會相等」歸結到一個可以檢查的矩陣性質。

## 不是 TU 也可能整數：MST 的 LP

MST 也能寫成 LP：對每個 `|S| ≥ 2` 的點集 S，`S` 內部的邊最多選 `|S| − 1` 條（不能成圈），全部的邊恰好選 `n − 1` 條。

```python
"""MST 的 LP（subtour 限制）：最優頂點是整數，值等於 Kruskal。"""
import random
from itertools import combinations

import networkx as nx
import numpy as np
from scipy.optimize import linprog

n = 5
G = nx.complete_graph(n)
E = list(G.edges)
rows = []                                  # 對每個 |S| >= 2 的點集：x(E(S)) <= |S| - 1
rhs = []
for k in range(2, n):                      # S = 全部點的那條改成等式（下面 A_eq）
    for S in combinations(range(n), k):
        rows.append([1 if u in S and v in S else 0 for u, v in E])
        rhs.append(k - 1)
A_ub = np.array(rows)

rng = random.Random(26)
agree = integral = 0
for trial in range(200):
    w = [rng.randint(1, 30) for _ in E]
    res = linprog(w, A_ub=A_ub, b_ub=rhs, A_eq=np.ones((1, len(E))), b_eq=[n - 1],
                  bounds=[(0, 1)] * len(E), method="highs-ds")
    for (u, v), x in zip(E, w):
        G[u][v]["weight"] = x
    agree += abs(res.fun - nx.minimum_spanning_tree(G).size(weight="weight")) < 1e-9
    integral += np.allclose(res.x, np.round(res.x))
print(f"K_{n}: LP value == MST weight in {agree}/200, integral LP vertex in {integral}/200")
# 但這個限制矩陣不是 TU：取 S = {0,1,2}, {0,1,3}, {0,2,3} 三列、邊 01, 02, 03 三行
sub = np.array([[1 if u in S and v in S else 0 for u, v in [(0, 1), (0, 2), (0, 3)]]
                for S in [(0, 1, 2), (0, 1, 3), (0, 2, 3)]])
print(sub.tolist(), "det =", round(np.linalg.det(sub)))
```

```text
K_5: LP value == MST weight in 200/200, integral LP vertex in 200/200
[[1, 1, 0], [1, 0, 1], [0, 1, 1]] det = -2
```

200 組隨機權重下 LP 的最優值都等於 MST，頂點都是整數——但第二行顯示限制矩陣**不是** TU，有一個 3×3 子矩陣行列式是 −2。所以 TU 是整數性的**充分條件，不是必要條件**。這個多面體的整數性來自 [Ch 25](./25-matroids.md) 提到的 matroid polytope 定理（Edmonds）：graphic matroid 的獨立集多面體由 `x(E(S)) ≤ |S| − 1` 這類 rank 不等式描述。更一般的工具叫 TDI（totally dual integral）系統，超出本章範圍。

另外注意限制數是指數級（`2^n` 個點集）。n = 5 時只有 26 條，所以能直接列出來；n 大時要用 separation oracle 只在需要時加入違反的限制（cutting plane）。真的要算 MST 時，Kruskal 當然快得多——LP 的價值在於**證明**和**統一**，不在於求解速度。

## 對比與取捨

| min-max 定理 | primal LP | dual LP | 整數性來源 |
| --- | --- | --- | --- |
| max-flow / min-cut | 流量，限制 = 有向關聯矩陣 + 容量 | 點位勢 + 邊切割量 | TU（定理 6） |
| König | 二分圖 matching | vertex cover | TU（定理 5） |
| 指派 / Egerváry | 完美 matching 的最小成本 | `max Σu + Σv`，`u_i + v_j ≤ a_ij` | TU（定理 5） |
| 最小費用流 | 流量 × 費用 | node potential，簡化費用 ≥ 0 | TU（定理 6） |
| MST / matroid | 獨立集多面體 | rank 不等式的對偶 | matroid polytope（非 TU） |
| 一般圖 matching | matching 多面體 | 含奇集變數的對偶 | 需要 blossom 不等式 |

最小費用流那一列值得回味：Ch 24 的 potential `h(v)` 就是對偶變數，「所有 residual 邊簡化費用 ≥ 0」就是對偶可行，「只在簡化費用為 0 的邊上有流量」就是互補鬆弛。SSP 是一個 primal-dual 演算法：始終維持對偶可行與互補鬆弛，逐步讓 primal 變可行。

什麼時候直接丟 LP solver、什麼時候用組合演算法？LP solver 通用、寫起來快、能加額外的線性限制（例如「流量加上預算上限」）；組合演算法快一到數個數量級、給出組合物件、沒有浮點誤差。原型階段用 LP，確定模型後換專用演算法，是常見的工程路線。

## 踩雷集錦

1. **以為 LP solver 一定回傳頂點**：HiGHS 的內點法在最優解不唯一時可能回傳面內部的點，即使 A 是 TU 也會看到 0.5 這種值。要整數解就用 simplex（`method="highs-ds"`），或者在 LP 之後做 crossover/rounding 並驗證。
2. **把 `marginals` 的正負號搞反**：`linprog` 永遠是最小化，`marginals` 是最小化目標對 b 的導數。你若是把 max 問題取負號去解，對偶值要再取一次負號（上面的 `−p.ineqlin.marginals`）。
3. **退化時把對偶值當成唯一的影子價格**：上面故意弄壞的例子裡，同一個 primal 最優點對應兩組不同的對偶最優解。敏感度分析只在非退化時有唯一答案。
4. **以為「LP 鬆弛的解是整數」只發生在 TU**：MST 的 LP 不是 TU 也有整數頂點。反過來，看到某次 LP 解剛好是整數，不代表限制矩陣有整數性保證——換一組權重可能就不是了。要嘛證明矩陣性質，要嘛每次檢查。
5. **一般圖套二分圖的 LP**：`C_5` 上 matching LP 給 2.5。把它四捨五入成 3 是不可行的 matching；捨去成 2 剛好是對的，但這不是一般規則。非二分圖的 matching 用 blossom（Ch 23）。

## 進階：再往深一層

- **LP 的演算法**：simplex 在實務上快，但存在讓它走指數步的例子（Klee–Minty 立方體）；第一個多項式時間演算法是 Khachiyan 的橢球法（1979），實務上常用的是內點法與 dual simplex。HiGHS 兩者都有。
- **整數規劃（ILP）**：限制矩陣不是 TU 時，整數版本通常是 NP-hard。branch-and-bound、cutting plane 都從 LP 鬆弛出發。`programming/sat_smt` 課的 [LRA 與 simplex](../../programming/sat_smt/27-lra-simplex.md) 和 [LIA 與 branch-and-bound](../../programming/sat_smt/28-lia-branch-bound.md) 是同一套想法在 SMT solver 裡的實作。
- **TU 的完整刻畫**：Seymour（1980）證明了每個 TU 矩陣都能由網路矩陣（及其轉置）與少數幾個特殊的小矩陣，經過特定的組合運算拼出來，並由此得到多項式時間的 TU 判定。本章的暴力判定是指數時間，只適合小矩陣。這裡只敘述結果，沒有證明。
- **對偶當作演算法設計工具**：primal-dual 方法（匈牙利法、SSP）、以及近似演算法裡的 LP rounding 與 dual fitting，都是「先寫對偶、再讓兩邊互相逼近」。

## 動手練習

1. 把 `flow_lp.py` 的網路改成 Ch 20 的範例網路，用 `method="highs-ipm"` 解對偶 LP，觀察回傳的 `d` 是否仍只有 0 和 1；若不是，解釋為什麼。
2. 寫出 Ch 24 指派問題的 LP 與其對偶，用 `linprog` 解 Ch 24 那個 3×3 矩陣，和 `hungarian` 回傳的 `u, v` 比較——它們是否相同？如果不同，兩者的值是否相等？
3. 用 `is_tu` 檢查一個偶圈 `C_6` 的關聯矩陣，再檢查一個含三角形但其他部分是二分的圖。用定理 5 的證明解釋結果。
4. 寫一個函式，輸入一個小圖，用 LP 求最大 matching，若 LP 解不是整數，找出造成分數的奇圈並印出來。

## 本章重點整理

- 弱對偶一行就能證，它讓任何可行的對偶解都成為上界，相等時就是最優性證書。
- 強對偶靠 Farkas 引理；互補鬆弛是「弱對偶不等式鏈每一項都取等號」。
- TU 矩陣 + 整數右手邊 ⇒ 所有頂點是整數（Cramer 法則）；二分圖與有向圖的關聯矩陣都是 TU。
- max-flow/min-cut、König、Egerváry、最小費用流的 potential，全是「TU 的 LP + 強對偶」；MST 的 LP 整數但不是 TU，靠的是 matroid polytope；一般圖 matching 需要 blossom 不等式。

## 自我檢核

- [ ] 不看筆記，寫出 (P) 的對偶並證明弱對偶，指出每個不等號用到哪個限制。
- [ ] 如果有人說「我的 LP 解出來是整數，所以這個問題的 LP 鬆弛是緊的」，你會怎麼反駁？
- [ ] 能用定理 5 的證明解釋為什麼 `C_5` 的關聯矩陣行列式不是 0？證明是在哪一步失敗的？
- [ ] 把 Ch 24 的 SSP 用「對偶可行 + 互補鬆弛」的語言重新描述一次。
- [ ] 想一個你工作中的資源分配問題，寫出它的 LP，並說明對偶變數在你的情境裡代表什麼價格。

## 延伸閱讀

### 論文

- **Integral Boundary Points of Convex Polyhedra** — Alan J. Hoffman、Joseph B. Kruskal，收錄於 H. W. Kuhn、A. W. Tucker 編《Linear Inequalities and Related Systems》（Annals of Mathematics Studies 38，Princeton University Press，1956），pp. 223–246
  - **核心貢獻**：刻畫「對所有整數右手邊都有整數頂點」的矩陣，提出全單模性。
  - **讀哪裡**：主定理的敘述；證明的線性代數部分和本章定理 4 同一個想法。
  - **和本章的關聯**：本章定理 4 的源頭。前提：本章與頂點的線性代數刻畫。

### 講義

- **[Lecture notes on linear programming and polyhedral combinatorics](https://math.mit.edu/~goemans/18433S13/polyhedral.pdf)** — Michel X. Goemans，MIT 18.433（2013）
  - **這份說什麼**：§3.1 Farkas 型的 Theorem of the Alternatives、§3.2 弱/強對偶（Theorem 3.3）與互補鬆弛（Theorem 3.4）、§3.3 面與頂點、§3.5 全單模性。
  - **讀哪裡**：§3.2 與 §3.5 對應本章；§3.4 把多面體觀點用到組合問題。
  - **為什麼值得讀**：短、證明完整、例子都是組合問題，和本課的角度最接近。

### 官方文件

- **[scipy.optimize.linprog](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linprog.html)**
  - **讀哪裡**：`method` 參數（`highs`、`highs-ds`、`highs-ipm`）與範例中 `ineqlin.marginals` 的說明。
  - **注意**：文件說舊的 `simplex`、`interior-point`、`revised simplex` 是 legacy 方法；本章只用 HiGHS 系列。

### 書籍

- **《Understanding and Using Linear Programming》** — Jiří Matoušek、Bernd Gärtner（Springer Universitext，2007）
  - **這本書的定位**：寫給理論 CS 學生的 LP 入門，幾何直覺強。
  - **讀哪幾章**：第 3 章「Integer Programming and LP Relaxation」對應本章 TU 與整數性落差；第 6 章「Duality of Linear Programming」從 Farkas 引理推導對偶定理，就我所知也給了 Farkas 引理本身的證明，可補上本章只給架構的部分。前提：線性代數。
- **《Combinatorial Optimization: Polyhedra and Efficiency》** — Alexander Schrijver（Springer，2003）
  - **這本書的定位**：本課 README 列的 Part 4 深水區百科；flow、matching、matroid 的多面體理論都有完整證明。
  - **讀哪幾章**：先讀前面關於 LP 與 TU 的預備章節，再依興趣跳到 matching polytope 或 matroid intersection。

Part 4 的理論到這裡收束：flow、matching、matroid 都是「好結構的 LP」。接下來的練習把它們變成工具——自己寫一個 flow 函式庫，然後把排班、選課、專案選擇、影像分割這些看起來無關的問題，全部建模成 flow。

→ [練習 D：Flow 函式庫與問題建模](./practice-d-flow-modeling.md)
