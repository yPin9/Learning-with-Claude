# 練習 E — 譜分群與相變實驗

> **目標**：把 Ch 27–33 拼起來，交出一個不依賴 scikit-learn 的小型函式庫 `spectral.py`：自刻 Laplacian、Fiedler 切割、sweep cut、k-means 與 Ng–Jordan–Weiss 譜分群，用在 Zachary 空手道俱樂部上並和真實的分裂比較；再自刻 `G(n, p)` 生成器與 union-find，重現巨大分量的相變並和 `1 − s = e^{−cs}` 對照。完成後你驗證了自己能把特徵值理論落地成可測試的程式，並且知道它在真實資料上錯在哪裡。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、pytest 9.2.0，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2 pytest==9.2.0`。在繁中 Windows 上跑範例時設定 `PYTHONUTF8=1`，避免輸出導到檔案時變成 cp950 亂碼。

## 背景與動機

1970 年代初，人類學家 Wayne Zachary 觀察了一所大學空手道俱樂部兩年，記錄 34 名成員之間在俱樂部之外的互動。觀察期間，教練（文獻裡稱 Mr. Hi）和俱樂部幹部（Officer）起了衝突，俱樂部最後分裂成兩個。Zachary 1977 年的論文問：**只看社交網路的結構，能不能預測每個人最後跟了誰？**這張 34 點、78 條邊的圖後來成了社群偵測（community detection）領域最常用的「Hello World」資料集，NetworkX 內建為 `nx.karate_club_graph()`，每個點的 `club` 屬性就是真實的分裂結果（`'Mr. Hi'` 或 `'Officer'`）。

這個練習模擬的是一個真實的工作流程：你拿到一張圖（服務呼叫圖、AD 權限圖、社交圖），想把它切成幾個「內部緊密、彼此稀疏」的群。Ch 28 告訴你 Fiedler 向量給出一個有理論保證的切割；這個練習讓你親手實作它、量它的品質，並且看到理論保證和「正確答案」之間的距離。

第二部分是 Ch 33 的延伸：從零寫一個 `G(n, p)` 生成器（不用 NetworkX），用它重現巨大分量的相變。這是做任何「隨機圖空模型」分析的基本功：你得能信任自己的生成器。

## 任務規格

### Part A：譜分群（`spectral.py`）

圖一律用鄰接字典 `adj: dict[node, set[node]]` 表示，節點可以是任何可排序的值（整數或字串）。所有輸出向量與矩陣的列順序都是 `sorted(adj)`。

| 函式 | 輸入 | 輸出 | 規格 |
| --- | --- | --- | --- |
| `laplacian(adj, normalized=False)` | 鄰接字典 | `(nodes, L)` | `L = D − A`；`normalized=True` 時 `I − D^{−1/2} A D^{−1/2}`。必須和 `nx.laplacian_matrix` / `nx.normalized_laplacian_matrix` 一致 |
| `fiedler(adj, normalized=False)` | 鄰接字典 | `(nodes, λ_2, v)` | v 是第二小特徵值的特徵向量；`normalized=True` 時回傳 `D^{−1/2} y`（y 是 N 的特徵向量）。**符號固定**成「第一個非零分量為正」，讓結果可重現 |
| `fiedler_cut(adj, normalized=False)` | 鄰接字典 | `(S, T)` | `S = {v ≥ 0}`、`T = {v < 0}` |
| `sweep_cut(adj, normalized=True)` | 鄰接字典 | `(S, φ)` | 沿 Fiedler 向量排序，回傳 conductance 最小的前綴（Ch 28） |
| `cut_size(adj, S)`、`conductance(adj, S)` | 鄰接字典、點集 | 整數、浮點 | `φ(S) = \|E(S,S̄)\| / min(vol S, vol S̄)`；一側為空時丟 `ValueError` |
| `kmeans(X, k, seed=0, n_init=10)` | `n×d` 陣列 | `(labels, inertia)` | Lloyd 演算法 + k-means++ 初始化，跑 `n_init` 次取 inertia 最小者；`k` 不在 `[1, n]` 時丟 `ValueError`；點全部重合時不可崩潰 |
| `spectral_clustering(adj, k, seed=0)` | 鄰接字典、群數 | `dict[node, int]` | Ng–Jordan–Weiss：取正規化 Laplacian 前 k 個特徵向量，每列正規化成單位長度，再做 k-means |
| `best_match_accuracy(pred, truth)` | 兩個 `dict[node, label]` | 浮點 | 兩群時，標籤互換取較好者 |

**非法輸入**：點數 < 2、不連通、有自環、鄰接不對稱，`fiedler` 一律丟 `ValueError`（不連通時 λ_2 = 0，Fiedler 向量沒有意義，見 Ch 28 踩雷 2）。

**禁止**：scikit-learn、`scipy.cluster`、`nx.fiedler_vector`、`nx.community`。允許 `numpy.linalg.eigh`（我們不自刻特徵值求解器）。NetworkX 只能用來載入資料與在測試裡對答案。

### Part B：ER 相變（同一個檔案）

| 函式 | 規格 |
| --- | --- |
| `gnp_edges(n, p, rng)` | 自刻 `G(n, p)`，回傳 `(u, v)` 串列且 `u < v`、無重複；期望時間 `O(n + m)`（提示：幾何跳躍，見 Ch 33 對 `fast_gnp_random_graph` 的說明）；`p ∉ [0, 1]` 丟 `ValueError` |
| `component_sizes(n, edges)` | union-find，回傳由大到小的分量大小（孤立點也算） |
| `giant_theory(c)` | `1 − s = e^{−cs}` 的最大根；`c ≤ 1` 回傳 0；誤差 `< 1e-9` |
| `phase_table(n, cs, trials, seed=0)` | 回傳 `[(c, mean L1/n, mean L2/n, theory)]`；相同參數必須產生相同結果（每個 `(c, trial)` 用確定的 seed） |

**效能要求**：`phase_table(10_000, 7 個 c 值, trials=10)` 在一般筆電上應在數秒內完成；參考解答在本機約 0.4 秒。

### 驗收標準

1. 下方 `test_spectral.py` 全部通過。
2. 在空手道俱樂部上，Fiedler 符號切割的準確率 ≥ 0.94，並能說出分錯的是哪幾個點、為什麼。
3. 相變表中 `c ≥ 1.5` 的每一列，模擬的 `L1/n` 和理論值差距 `< 0.01`（n = 10000、10 次試驗）。

## 期望輸出範例

跑 `run_karate.py`（參考解答附在後面）：

```text
輸入：nx.karate_club_graph()（34 點、78 條邊、每點有 club 屬性）
輸出：
Mr. Hi: 17  Officer: 17  edges: 78
sign cut (L) l2=0.4685 |S|=15 cut=10 phi=0.1515 acc=0.9412 misclassified=[2, 8]
sign cut (N) l2=0.1323 |S|=15 cut=10 phi=0.1515 acc=0.9412 misclassified=[2, 8]
sweep cut (N)          |S|=18 cut=10 phi=0.1316 acc=0.9706 misclassified=[8]
ground truth           |S|=17 cut=11 phi=0.1467 acc=1.0000 misclassified=[]
nx default (weight='weight'): 1.1871
nx weight=None              : 0.4685
nx weight=None, normalized  : 0.1323
NJW k=2: [19 nodes: 2 Hi / 17 Officer]  [15 nodes: 15 Hi / 0 Officer]  acc=0.9412
NJW k=3: [18 nodes: 1 Hi / 17 Officer]  [11 nodes: 11 Hi / 0 Officer]  [5 nodes: 5 Hi / 0 Officer]
NJW k=4: [12 nodes: 1 Hi / 11 Officer]  [11 nodes: 11 Hi / 0 Officer]  [6 nodes: 0 Hi / 6 Officer]  [5 nodes: 5 Hi / 0 Officer]
```

跑 `run_phase.py`：

```text
n=10000, trials=10, seed=0
   c    L1/n    L2/n  theory  |diff|
 0.5  0.0019  0.0016  0.0000  0.0019
 0.8  0.0067  0.0050  0.0000  0.0067
 1.0  0.0482  0.0230  0.0000  0.0482
 1.2  0.3084  0.0074  0.3137  0.0053
 1.5  0.5852  0.0026  0.5828  0.0023
 2.0  0.7978  0.0010  0.7968  0.0009
 3.0  0.9408  0.0004  0.9405  0.0003
elapsed 0.4 s
```

邊界輸入：

```text
輸入：fiedler({0: {1}, 1: {0}, 2: {3}, 3: {2}})      （兩個不相連的 K2）
輸出：ValueError: graph is not connected

輸入：kmeans(np.zeros((3, 2)), 4)
輸出：ValueError: need 1 <= k <= number of points

輸入：gnp_edges(10, 1.0, rng)
輸出：45 條邊（完全圖 K10）
```

## 如果你卡住了

1. **λ_2 和 `nx.algebraic_connectivity` 對不上？** 先看你的答案是不是 0.4685、而 NetworkX 給 1.1871。`karate_club_graph()` 在 NetworkX 3.x 的邊帶有 `weight` 屬性（互動次數），而 `algebraic_connectivity` 預設 `weight="weight"`。你的 `adj` 丟掉了權重，所以對拍時要傳 `weight=None`。這是本練習最常見的「我的程式沒錯，是對拍方式錯」。
2. **每次跑出來 S 和 T 互換？** 特徵向量乘 −1 仍是特徵向量，LAPACK 不保證符號。規格要求「第一個非零分量為正」，就是為了讓測試能寫成確定性的斷言。
3. **正規化版本的切割怪怪的？** 正規化 Laplacian 的特徵向量 y 活在 `D^{1/2}` 加權的座標裡；Cheeger 的 sweep 要用 `z = D^{−1/2} y` 排序（Ch 28「困難方向」一節）。忘了這一步，高度數的點會被系統性地擠到中間。
4. **k-means 偶爾回傳一個空群、或出現 `nan`？** 某個中心沒有分到任何點時，`X[labels == j].mean()` 會是空陣列的平均。保留舊中心即可；k-means++ 在所有點重合時 `d2.sum() == 0`，要退回均勻抽樣。
5. **`G(n, p)` 自刻版太慢？** 逐對擲 `C(n, 2)` 次硬幣在 n = 10000 時是 5000 萬次，Python 要幾十秒。幾何跳躍：下一條出現的邊之前會跳過 `⌊ln(1 − r) / ln(1 − p)⌋` 條不出現的邊（r 均勻於 [0, 1)），把「第幾對」換算回 `(w, v)`。

## 實作步驟建議

### Step 1：Laplacian 與輸入檢查

寫 `laplacian` 與 `_check_graph`。先過 `test_laplacian_matches_networkx` 與 `test_invalid_graphs_raise`。子目標：五種非法輸入都丟 `ValueError`，而不是在 `eigh` 裡產生 `nan` 或靜默回傳垃圾。

### Step 2：Fiedler 向量與符號切割

`np.linalg.eigh` 由小到大回傳，取 `vecs[:, 1]`（不是 `vecs[1]`，見 Ch 28 踩雷 1）。固定符號。在兩個 K5 以一條橋相連的圖、以及 `P_8` 上確認切割正確。子目標：`test_two_cliques_split_perfectly`、`test_path_splits_in_the_middle`、`test_lambda2_matches_networkx` 通過。

### Step 3：conductance 與 sweep cut

實作 `cut_size`、`conductance`、`sweep_cut`。子目標：在空手道俱樂部上，sweep 找到的 φ 不大於符號切割的 φ，而且不超過 Cheeger 上界 `√(2ν_2)`。

### Step 4：k-means 與 NJW 譜分群

先在兩團高斯點雲上測 k-means（`test_kmeans_separated_blobs`），再處理邊界（k 非法、點全重合）。然後組出 `spectral_clustering`，在三個 K6 串成一條鏈的圖上確認 k = 3 時三群正確。

### Step 5：空手道俱樂部分析

寫 `run_karate.py`：印出 L 與 N 兩種符號切割、sweep cut、真實分裂各自的 cut、φ、準確率與分錯的點；再跑 k = 2、3、4 的 NJW，看每一群的組成。子目標：能解釋每一個分錯的點。

### Step 6：G(n, p)、union-find 與相變表

實作 `gnp_edges`（先寫逐對版本當參考、再寫幾何跳躍版，兩者在小 n 上比邊數分佈）、`component_sizes`（和 `nx.connected_components` 對拍）、`giant_theory`、`phase_table`。子目標：`run_phase.py` 的 `c ≥ 1.5` 各列誤差 < 0.01。

### Step 7：整合與測試

跑 `pytest -q test_spectral.py`，全部通過後，回頭把 Step 5、6 的觀察寫成三到五句結論。

## 測試檔（規格的一部分，請原封不動地使用）

```python
import math
import random

import networkx as nx
import numpy as np
import pytest

from spectral import (best_match_accuracy, component_sizes, conductance, cut_size, fiedler,
                      fiedler_cut, giant_theory, gnp_edges, kmeans, laplacian, phase_table,
                      spectral_clustering, sweep_cut)


def to_adj(G):
    return {u: set(G[u]) for u in G}


def two_cliques(k=5, bridges=1):
    G = nx.disjoint_union(nx.complete_graph(k), nx.complete_graph(k))
    G.add_edges_from((i, k + i) for i in range(bridges))
    return G


# ---------- Part A ----------

def test_laplacian_matches_networkx():
    G = nx.petersen_graph()
    nodes, L = laplacian(to_adj(G))
    assert np.allclose(L, nx.laplacian_matrix(G, nodelist=nodes).toarray())
    _, N = laplacian(to_adj(G), normalized=True)
    assert np.allclose(N, nx.normalized_laplacian_matrix(G, nodelist=nodes).toarray())


@pytest.mark.parametrize("G", [nx.path_graph(7), nx.cycle_graph(9), nx.petersen_graph(), two_cliques()])
def test_lambda2_matches_networkx(G):
    _, lam2, _ = fiedler(to_adj(G))
    assert lam2 == pytest.approx(nx.algebraic_connectivity(G, weight=None, seed=0), abs=1e-6)


def test_two_cliques_split_perfectly():
    S, T = fiedler_cut(to_adj(two_cliques(5, 1)))
    assert {frozenset(S), frozenset(T)} == {frozenset(range(5)), frozenset(range(5, 10))}


def test_path_splits_in_the_middle():
    S, T = fiedler_cut(to_adj(nx.path_graph(8)))
    assert sorted(map(sorted, (S, T))) == [[0, 1, 2, 3], [4, 5, 6, 7]]


def test_sign_is_deterministic():
    adj = to_adj(two_cliques())
    a = fiedler(adj)[2]
    b = fiedler({u: set(adj[u]) for u in reversed(sorted(adj))})[2]
    assert np.allclose(a, b)


def test_non_string_and_string_labels():
    G = nx.relabel_nodes(two_cliques(), {i: f"n{i:02d}" for i in range(10)})
    S, _ = fiedler_cut(to_adj(G))
    assert len(S) == 5


@pytest.mark.parametrize("adj", [
    {},                                        # 空圖
    {0: set()},                                # 單點
    {0: {1}, 1: {0}, 2: {3}, 3: {2}},          # 不連通
    {0: {1}, 1: set()},                        # 不對稱
    {0: {0, 1}, 1: {0}},                       # 自環
])
def test_invalid_graphs_raise(adj):
    with pytest.raises(ValueError):
        fiedler(adj)


def test_conductance_and_cut():
    adj = to_adj(two_cliques(5, 2))
    S = set(range(5))
    assert cut_size(adj, S) == 2
    assert conductance(adj, S) == pytest.approx(2 / (5 * 4 + 2))
    with pytest.raises(ValueError):
        conductance(adj, set())


def test_kmeans_separated_blobs():
    rng = np.random.default_rng(1)
    X = np.vstack([rng.normal(0, 0.1, (20, 2)), rng.normal(5, 0.1, (20, 2))])
    labels, inertia = kmeans(X, 2, seed=0)
    assert len(set(labels[:20])) == 1 and len(set(labels[20:])) == 1 and labels[0] != labels[20]
    assert inertia < 2.0


def test_kmeans_bad_k():
    with pytest.raises(ValueError):
        kmeans(np.zeros((3, 2)), 4)
    with pytest.raises(ValueError):
        kmeans(np.zeros((3, 2)), 0)


def test_kmeans_duplicate_points():
    labels, inertia = kmeans(np.zeros((5, 2)), 2, seed=0)   # 全部重合：不能除以 0 或丟例外
    assert inertia == 0.0 and len(labels) == 5


def test_spectral_clustering_three_cliques():
    G = nx.disjoint_union_all([nx.complete_graph(6)] * 3)
    G.add_edges_from([(0, 6), (6, 12)])
    lab = spectral_clustering(to_adj(G), 3, seed=0)
    groups = {frozenset(u for u in lab if lab[u] == c) for c in set(lab.values())}
    assert groups == {frozenset(range(i, i + 6)) for i in (0, 6, 12)}


def test_karate_accuracy_and_sweep():
    G = nx.karate_club_graph()
    adj = to_adj(G)
    truth = {u: G.nodes[u]["club"] for u in G}
    S, _ = fiedler_cut(adj)
    assert best_match_accuracy({u: int(u in S) for u in adj}, truth) >= 0.94
    S2, phi = sweep_cut(adj)
    assert phi <= conductance(adj, S) + 1e-12
    assert phi <= math.sqrt(2 * fiedler(adj, normalized=True)[1])      # Cheeger 困難方向


# ---------- Part B ----------

def test_giant_theory_values():
    assert giant_theory(0.5) == 0.0 and giant_theory(1.0) == 0.0
    for c in (1.01, 1.5, 2.0, 5.0):
        s = giant_theory(c)
        assert 0 < s < 1 and abs(1 - s - math.exp(-c * s)) < 1e-9
    assert giant_theory(2.0) == pytest.approx(0.7968121, abs=1e-6)


def test_gnp_edge_count_and_simple():
    rng = random.Random(0)
    n, p = 400, 0.05
    counts = []
    for _ in range(30):
        E = gnp_edges(n, p, rng)
        assert all(0 <= u < v < n for u, v in E) and len(set(E)) == len(E)
        counts.append(len(E))
    mean, expected = sum(counts) / len(counts), p * n * (n - 1) / 2
    sd = math.sqrt(expected * (1 - p) / len(counts))
    assert abs(mean - expected) < 4 * sd


def test_gnp_extremes():
    rng = random.Random(0)
    assert gnp_edges(10, 0.0, rng) == []
    assert len(gnp_edges(10, 1.0, rng)) == 45
    with pytest.raises(ValueError):
        gnp_edges(10, 1.5, rng)


def test_component_sizes_match_networkx():
    G = nx.fast_gnp_random_graph(2000, 1.3 / 2000, seed=7)
    assert component_sizes(2000, G.edges()) == sorted(map(len, nx.connected_components(G)), reverse=True)
    assert component_sizes(3, []) == [1, 1, 1]


def test_phase_table_shape_and_reproducible():
    a = phase_table(2000, [0.5, 2.0], trials=3, seed=1)
    b = phase_table(2000, [0.5, 2.0], trials=3, seed=1)
    assert a == b
    (c0, l1_sub, _, _), (c1, l1_sup, l2_sup, th) = a
    assert l1_sub < 0.05 and abs(l1_sup - th) < 0.05 and l2_sup < 0.05
```

```text
$ PYTHONUTF8=1 python -m pytest -q test_spectral.py
.........................                                                [100%]
25 passed in 0.70s
```

（25 是把 `parametrize` 展開後的測試數；時間在本機量測。）

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

`spectral.py`：

```python
"""練習 E 參考解答：譜分群與 ER 相變實驗。"""
from __future__ import annotations

import math
import random

import numpy as np


# ---------- Part A：譜分群 ----------

def _check_graph(adj):
    if len(adj) < 2:
        raise ValueError("need at least 2 nodes")
    for u in adj:
        for v in adj[u]:
            if v == u:
                raise ValueError(f"self-loop at {u!r}")
            if u not in adj.get(v, ()):
                raise ValueError(f"asymmetric edge {u!r}-{v!r}")
    # BFS 檢查連通：不連通時 lambda_2 = 0，Fiedler 向量沒有意義
    start = next(iter(adj))
    seen, stack = {start}, [start]
    while stack:
        for w in adj[stack.pop()]:
            if w not in seen:
                seen.add(w)
                stack.append(w)
    if len(seen) != len(adj):
        raise ValueError("graph is not connected")


def laplacian(adj, normalized=False):
    """回傳 (nodes, L)。normalized=True 時為 I - D^{-1/2} A D^{-1/2}。"""
    nodes = sorted(adj)
    idx = {v: i for i, v in enumerate(nodes)}
    n = len(nodes)
    A = np.zeros((n, n))
    for u in nodes:
        for v in adj[u]:
            A[idx[u], idx[v]] = 1.0
    d = A.sum(axis=1)
    if normalized:
        inv = 1 / np.sqrt(d)
        return nodes, np.eye(n) - inv[:, None] * A * inv[None, :]
    return nodes, np.diag(d) - A


def fiedler(adj, normalized=False):
    """回傳 (nodes, lambda_2, v_2)；v_2 的符號固定成「第一個非零分量為正」。"""
    _check_graph(adj)
    nodes, L = laplacian(adj, normalized)
    lam, vecs = np.linalg.eigh(L)
    v = vecs[:, 1]
    if normalized:                      # 回到原座標：z = D^{-1/2} y
        d = np.array([len(adj[u]) for u in nodes], dtype=float)
        v = v / np.sqrt(d)
    nz = np.flatnonzero(np.abs(v) > 1e-12)
    if nz.size and v[nz[0]] < 0:
        v = -v
    return nodes, float(lam[1]), v


def fiedler_cut(adj, normalized=False):
    """依 Fiedler 向量的符號切成兩群（v >= 0 一群、v < 0 一群）。"""
    nodes, _, v = fiedler(adj, normalized)
    S = {u for u, x in zip(nodes, v) if x >= 0}
    return S, set(nodes) - S


def cut_size(adj, S):
    return sum(1 for u in S for w in adj[u] if w not in S)


def conductance(adj, S):
    vol_S = sum(len(adj[u]) for u in S)
    vol_all = sum(len(adj[u]) for u in adj)
    denom = min(vol_S, vol_all - vol_S)
    if denom == 0:
        raise ValueError("empty side")
    return cut_size(adj, S) / denom


def kmeans(X, k, seed=0, n_init=10, max_iter=300):
    """Lloyd 演算法 + k-means++ 初始化；回傳 (labels, inertia)。"""
    X = np.asarray(X, dtype=float)
    n = X.shape[0]
    if not 1 <= k <= n:
        raise ValueError("need 1 <= k <= number of points")
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(n_init):
        centers = [X[rng.integers(n)]]
        for _ in range(1, k):            # k-means++：依距離平方比例挑下一個中心
            d2 = np.min([((X - c) ** 2).sum(axis=1) for c in centers], axis=0)
            probs = d2 / d2.sum() if d2.sum() > 0 else np.full(n, 1 / n)
            centers.append(X[rng.choice(n, p=probs)])
        C = np.array(centers)
        for _ in range(max_iter):
            dist = ((X[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)
            labels = dist.argmin(axis=1)
            newC = np.array([X[labels == j].mean(axis=0) if np.any(labels == j) else C[j]
                             for j in range(k)])
            if np.allclose(newC, C):
                break
            C = newC
        inertia = float(((X - C[labels]) ** 2).sum())
        if best is None or inertia < best[1] - 1e-12:
            best = (labels.copy(), inertia)
    return best


def spectral_clustering(adj, k, seed=0):
    """Ng–Jordan–Weiss：正規化 Laplacian 前 k 個特徵向量，列正規化後做 k-means。"""
    _check_graph(adj)
    nodes, N = laplacian(adj, normalized=True)
    _, vecs = np.linalg.eigh(N)
    U = vecs[:, :k]
    U = U / np.linalg.norm(U, axis=1, keepdims=True)
    labels, _ = kmeans(U, k, seed=seed)
    return {u: int(c) for u, c in zip(nodes, labels)}


def best_match_accuracy(pred, truth):
    """兩群時：標籤可以互換，取兩種對應中較好的一種。"""
    keys = sorted(truth)
    classes = sorted(set(truth.values()))
    if len(classes) != 2 or len(set(pred.values())) != 2:
        raise ValueError("two clusters expected")
    a = sorted(set(pred.values()))
    agree = sum((pred[u] == a[0]) == (truth[u] == classes[0]) for u in keys)
    return max(agree, len(keys) - agree) / len(keys)


# ---------- Part B：ER 相變 ----------

def gnp_edges(n, p, rng):
    """自刻 G(n,p)：幾何跳躍法（Batagelj–Brandes），期望時間 O(n + m)。"""
    if not 0 <= p <= 1:
        raise ValueError("p must be in [0, 1]")
    if p == 0:
        return []
    if p == 1:
        return [(u, v) for v in range(n) for u in range(v)]
    edges, lp = [], math.log(1 - p)
    v, w = 1, -1
    while v < n:
        w += 1 + int(math.log(1 - rng.random()) / lp)
        while w >= v and v < n:
            w -= v
            v += 1
        if v < n:
            edges.append((w, v))
    return edges


def component_sizes(n, edges):
    parent, size = list(range(n)), [1] * n

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for u, v in edges:
        ru, rv = find(u), find(v)
        if ru != rv:
            if size[ru] < size[rv]:
                ru, rv = rv, ru
            parent[rv] = ru
            size[ru] += size[rv]
    return sorted((size[r] for r in range(n) if find(r) == r), reverse=True)


def giant_theory(c, tol=1e-12):
    """1 - s = exp(-c s) 的最大根；c <= 1 回傳 0。用二分法（比不動點迭代在 c≈1 時穩）。"""
    if c <= 1:
        return 0.0
    lo, hi = 1e-15, 1.0
    f = lambda s: 1 - math.exp(-c * s) - s      # noqa: E731
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def phase_table(n, cs, trials, seed=0):
    """回傳 [(c, mean L1/n, mean L2/n, theory)]；每個 (c, trial) 用獨立的 seed。"""
    rows = []
    for i, c in enumerate(cs):
        L1 = L2 = 0.0
        for t in range(trials):
            rng = random.Random(seed * 1_000_003 + i * 1009 + t)
            sizes = component_sizes(n, gnp_edges(n, c / n, rng)) + [0]
            L1 += sizes[0] / n
            L2 += sizes[1] / n
        rows.append((c, L1 / trials, L2 / trials, giant_theory(c)))
    return rows


def sweep_cut(adj, normalized=True):
    """沿 Fiedler 向量排序，回傳 conductance 最小的前綴 (S, phi)。"""
    nodes, _, v = fiedler(adj, normalized)
    order = [nodes[i] for i in np.argsort(v, kind="stable")]
    best_S, best_phi = None, math.inf
    S = set()
    for u in order[:-1]:
        S.add(u)
        phi = conductance(adj, S)
        if phi < best_phi - 1e-15:
            best_S, best_phi = set(S), phi
    return best_S, best_phi
```

`run_karate.py`：

```python
import networkx as nx
import numpy as np

from spectral import (best_match_accuracy, conductance, cut_size, fiedler, fiedler_cut,
                      spectral_clustering, sweep_cut)

G = nx.karate_club_graph()
adj = {u: set(G[u]) for u in G}
truth = {u: G.nodes[u]["club"] for u in G}
hi = {u for u in adj if truth[u] == "Mr. Hi"}
print("Mr. Hi:", len(hi), " Officer:", len(adj) - len(hi), " edges:", G.number_of_edges())


def report(name, S):
    side = S if len(S & hi) >= len(S - hi) else set(adj) - S      # 讓 side 代表「Mr. Hi 那一群」
    wrong = sorted(side ^ hi)
    pred = {u: int(u in S) for u in adj}
    print(f"{name:22s} |S|={len(S):2d} cut={cut_size(adj, S):2d} phi={conductance(adj, S):.4f} "
          f"acc={best_match_accuracy(pred, truth):.4f} misclassified={wrong}")


for normalized in (False, True):
    _, lam2, _ = fiedler(adj, normalized)
    S, _ = fiedler_cut(adj, normalized)
    report(f"sign cut ({'N' if normalized else 'L'}) l2={lam2:.4f}", S)
S, _ = sweep_cut(adj, normalized=True)
report("sweep cut (N)", S)
report("ground truth", hi)

print("nx default (weight='weight'):", round(nx.algebraic_connectivity(G, seed=0), 4))
print("nx weight=None              :", round(nx.algebraic_connectivity(G, weight=None, seed=0), 4))
print("nx weight=None, normalized  :", round(nx.algebraic_connectivity(G, weight=None, normalized=True, seed=0), 4))

for k in (2, 3, 4):
    lab = spectral_clustering(adj, k, seed=0)
    parts = []
    for c in range(k):
        mem = [u for u in lab if lab[u] == c]
        parts.append((len(mem), sum(u in hi for u in mem)))
    parts.sort(reverse=True)
    desc = "  ".join(f"[{m} nodes: {h} Hi / {m - h} Officer]" for m, h in parts)
    extra = f"  acc={best_match_accuracy(lab, truth):.4f}" if k == 2 else ""
    print(f"NJW k={k}: {desc}{extra}")
```

`run_phase.py`：

```python
import time

from spectral import phase_table

t0 = time.perf_counter()
n, trials = 10_000, 10
rows = phase_table(n, [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0], trials, seed=0)
print(f"n={n}, trials={trials}, seed=0")
print(f"{'c':>4s} {'L1/n':>7s} {'L2/n':>7s} {'theory':>7s} {'|diff|':>7s}")
for c, l1, l2, th in rows:
    print(f"{c:4.1f} {l1:7.4f} {l2:7.4f} {th:7.4f} {abs(l1 - th):7.4f}")
print(f"elapsed {time.perf_counter() - t0:.1f} s")
```

**解答說明**：

- **輸入檢查放在 `fiedler` 而不是 `laplacian`**：Laplacian 對不連通的圖完全有定義（Ch 28 定理 28.2 就是在講它），是「取第二個特徵向量」這件事在不連通時沒有意義。把檢查放在真正需要假設的地方。
- **符號正規化**：「第一個非零分量為正」讓 `fiedler` 成為確定性函式。注意它依賴 `sorted(adj)` 的順序，所以節點要可排序；混用整數與字串節點會在 `sorted` 丟 `TypeError`，這是刻意不處理的限制。
- **正規化版本回到原座標**：`fiedler(..., normalized=True)` 回傳 `D^{−1/2} y`。符號切割不受影響（`D^{−1/2}` 是正的對角矩陣，不改變符號），但 sweep 的排序會受影響。
- **k-means 的空群處理**：保留舊中心是最簡單的做法；scikit-learn 的做法是把最遠的點搬過去當新中心。兩者在本練習的資料上沒有差別。
- **NJW 的列正規化**：每個點在 `R^k` 的嵌入除以自己的長度，讓度數很低的點（嵌入長度很短）不會全部擠在原點附近被同一個中心吃掉。這一步是 Ng–Jordan–Weiss 和「直接用 `L` 的特徵向量做 k-means」的主要差別。
- **`giant_theory` 用二分法**：不動點迭代 `s ← 1 − e^{−cs}` 在 c 接近 1 時收斂極慢（收斂率是 `c(1−s)`，趨近 1）；二分法在 `[1e-15, 1]` 上固定 50 次左右就達到 `1e-12`。下界不用 0 是因為 `f(0) = 0` 是另一個根。
- **`phase_table` 的 seed**：每個 `(c, trial)` 由 `(seed, i, t)` 決定一個獨立的 `random.Random`，所以加減 c 值不會改變其他列的結果——如果你用一個共享的 `rng` 依序生成，插入一個 c 值會讓後面所有列都變。

### 結果解讀

- **Fiedler 符號切割準確率 0.9412（34 人分錯 2 人：點 2 與 點 8）**，L 與 N 兩個版本給出相同的切割。點 8 有 5 個鄰居，其中 3 個屬於 Officer 派、2 個屬於 Mr. Hi 派，但他最後跟了 Mr. Hi；點 2 有 10 個鄰居，4 個在 Officer 派。**任何只看邊的方法都很難把點 8 分對**——網路結構裡沒有足夠的訊號，這是資料的極限，不是演算法的 bug。
- **sweep cut 的 φ = 0.1316 比真實分裂的 0.1467 還小**，準確率反而提高到 0.9706（只錯點 8）。這說明了兩件事：(1) sweep 確實在做它保證的事——找低 conductance 的切割；(2) 真實的社會分裂**不是**最低 conductance 的切割。目標函數和「正確答案」之間有落差，這在任何真實的分群問題裡都存在。
- **λ_2 對拍的教訓**：`nx.algebraic_connectivity(G)` 預設用邊權重，得 1.1871；`weight=None` 才和我們的 0.4685 一致。在你自己的資料上，先確認「圖」到底是不是加權的。
- **k = 3、4**：NJW 把 Mr. Hi 那一派再切成 11 人與 5 人兩群（5 人那群是點 4、5、6、10、16，它們和外界的 4 條邊全部連到教練點 0——一個只靠教練維繫的小圈子），Officer 派在 k = 4 時再分出一個 6 人的子群。每一群的組成幾乎是「純」的（至多 1 個異類）——更細的分群沒有跨越真實的分裂線，表示兩派內部也有子結構。
- **相變表**：c ≥ 1.2 時誤差 ≤ 0.0053，c ≥ 1.5 時 ≤ 0.0023。c = 1.0 的 0.0482 是臨界尺度 `n^{2/3}` 的有限尺寸效應（Ch 33）：`10000^{2/3} / 10000 ≈ 0.046`。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
| --- | --- | --- |
| 兩個 K5 以一條橋相連 | `{0..4}` 與 `{5..9}` | 最基本的正確性 |
| `P_8` | `{0,1,2,3}` 與 `{4,5,6,7}` | 沒有群結構時，從中間切開 |
| 字串節點版的兩個 K5 | 一側 5 個點 | 節點不必是整數 |
| 兩個 K5 以兩條邊相連，S = 一邊 | cut = 2，`φ = 2/22` | conductance 定義 |
| 三個 K6 串成鏈，k = 3 | 三群各 6 點 | NJW 多群 |
| 空手道俱樂部 | 準確率 ≥ 0.94；sweep 的 φ ≤ 符號切割的 φ ≤ ... ≤ `√(2ν_2)` | 真實資料 |
| `{}`、`{0: set()}` | `ValueError` | 點數 < 2 |
| 兩個不相連的 K2 | `ValueError: graph is not connected` | 不連通 |
| `{0: {1}, 1: set()}` | `ValueError` | 不對稱 |
| `{0: {0, 1}, 1: {0}}` | `ValueError` | 自環 |
| `kmeans(X, 0)`、`kmeans(X, n+1)` | `ValueError` | k 非法 |
| `kmeans(np.zeros((5, 2)), 2)` | inertia = 0，不崩潰 | 點全重合 |
| `gnp_edges(10, 0.0)`、`gnp_edges(10, 1.0)` | 0 條、45 條 | 極端 p |
| `gnp_edges(10, 1.5)` | `ValueError` | 非法 p |
| `gnp_edges(400, 0.05)` × 30 次 | 平均邊數在期望值 ± 4 個標準誤內 | 生成器的分佈正確 |
| `giant_theory(2.0)` | `0.7968121…` | 理論值 |
| `phase_table` 同參數跑兩次 | 結果完全相同 | 可重現性 |
| `phase_table(10_000, …, trials=10)` | 數秒內完成 | 效能邊界 |

## 延伸挑戰（加分）

- **加權版本**：讓 `laplacian` 接受 `dict[node, dict[node, weight]]`，用 karate club 的 `weight` 屬性重跑。準確率會變好還是變差？點 8 的命運有沒有改變？
- **大圖**：把 `np.linalg.eigh` 換成 `scipy.sparse.linalg.eigsh(L, k=2, which="SM")` 或 `lobpcg`，在 `nx.random_partition_graph([5000, 5000], 0.01, 0.0005, seed=0)` 這種「植入兩群」的圖上跑，量測時間並和稠密版比較。注意 `which="SM"` 在接近奇異的矩陣上收斂很慢，試試 shift-invert（`sigma=0` 附近）的取捨。
- **隨機區塊模型的偵測門檻**：在兩群各 n/2 點、群內機率 `a/n`、群間 `b/n` 的 SBM 上，`(a − b)² > 2(a + b)` 是能否優於隨機猜測的已知門檻（Kesten–Stigum 門檻）。用你的譜分群在 n = 4000 上掃過 a、b，畫出準確率對 `(a−b)²/(2(a+b))` 的圖，觀察相變——而且你會發現普通的 Laplacian 譜方法在稀疏時**達不到**這個門檻，這是 non-backtracking 矩陣方法出現的原因。
- **相變的有限尺寸縮放**：把 `phase_table` 改成在 `c = 1 + x · n^{−1/3}` 上取樣（x ∈ [−2, 2]），對 n = 2000、8000、32000 畫 `L1 / n^{2/3}` 對 x 的圖。三條曲線應該大致疊在一起——這就是臨界視窗的縮放律。

## 自我檢核

- [ ] 能不看解答，解釋為什麼 `fiedler` 要拒絕不連通的圖，而 `laplacian` 不用。
- [ ] 能說出空手道俱樂部上分錯的兩個點各自的鄰居組成，並解釋為什麼「分錯」不一定是演算法的問題。
- [ ] 能解釋 sweep cut 的 φ 比真實分裂還小這件事，對「用 conductance 當分群目標」意味著什麼。
- [ ] 能說出 `nx.algebraic_connectivity` 和你的 λ_2 對不上的原因，以及一般化的教訓（對拍時兩邊的輸入真的是同一個東西嗎？）。
- [ ] 能說出自己的實作和參考解答的差異，並解釋各自取捨（例如空群處理、符號正規化、seed 管理）。

## 延伸閱讀

- **[A tutorial on spectral clustering](https://arxiv.org/abs/0711.0189)** — Ulrike von Luxburg，Statistics and Computing 17(4)（2007），395–416
  - **這篇說什麼**：三種 Laplacian（未正規化、對稱正規化、隨機遊走）各自對應哪一種切割目標，以及 NJW、Shi–Malik 兩種演算法的差別與實務建議。
  - **讀哪裡**：Section 3（三種 Laplacian 與它們的性質）、Section 4（演算法）、Section 5（graph cut 觀點，直接接 Ch 28）、Section 8（實務問題：怎麼建圖、怎麼選 k）。
  - **為什麼值得讀**：目前最常被引用的譜分群入門；本練習的 Part A 幾乎就是它 Section 4 的實作。
- **[On spectral clustering: Analysis and an algorithm](https://papers.nips.cc/paper/2092-on-spectral-clustering-analysis-and-an-algorithm)** — Andrew Y. Ng、Michael I. Jordan、Yair Weiss，NIPS 14（2001），849–856
  - **核心貢獻**：本練習 `spectral_clustering` 的演算法（含列正規化那一步），以及它在「理想分群」附近為什麼有效的矩陣擾動分析。
  - **讀哪裡**：開頭的演算法框（五個步驟），以及接下來說明「理想情況下特徵向量長什麼樣」的分析直覺；擾動理論的細節可略。
- **An information flow model for conflict and fission in small groups** — Wayne W. Zachary，Journal of Anthropological Research 33(4)（1977），452–473
  - **這篇說什麼**：空手道俱樂部資料的原始出處；Zachary 用最大流最小割（Ch 20）模型預測分裂，和本練習用的譜方法是不同的切割思路。
  - **讀哪裡**：資料收集的描述與 flow 模型的結果段落。我們沒有找到免費的全文網址；JSTOR 上可以找到，NetworkX 的 `karate_club_graph` 文件也引用它。
- **[Normalized cuts and image segmentation](https://doi.org/10.1109/34.868688)** — Jianbo Shi、Jitendra Malik，IEEE TPAMI 22(8)（2000），888–905
  - **這篇說什麼**：normalized cut 目標與它的譜放寬，是本練習 conductance 與正規化版本的工程源頭（Ch 28 已列）。
  - **讀哪裡**：Section 2 的推導；比較它和 NJW 在「用幾個特徵向量、怎麼離散化」上的差別。

Part 5 到此結束：你已經能用特徵值切圖、用隨機遊走排序、用機率證明存在、用相變理解隨機網路。Part 6 換一個問題：有些圖問題為什麼**沒有**有效率的演算法？我們從 NP-complete 的歸約開始。

→ [Ch 34 NP-complete 圖問題與歸約](./34-np-complete-reductions.md)
