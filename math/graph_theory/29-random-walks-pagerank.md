# Ch 29 — 隨機遊走與 PageRank

> **目標**：把圖上的隨機遊走寫成 Markov chain，證明它的穩態分佈正比於度數、說明它什麼時候收斂、收斂多快（mixing time 由 spectral gap 控制）；最後自刻 PageRank 冪迭代，處理 dangling node，並和 `nx.pagerank` 對拍到 `1e-12`。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、SciPy 1.18.1，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

前兩章把圖變成矩陣，問的是靜態問題：有幾條 walk、怎麼切最便宜。這一章讓矩陣「動起來」：一個人站在某個點上，每一步隨機挑一條鄰邊走過去。這個簡單的過程同時是：

- **一個演算法工具**：在大圖上做均勻取樣、估計點數、近似計數（MCMC），都靠讓 walk 跑到「忘記起點」再取樣。要跑多久？這就是 mixing time。
- **一個網路分析工具**：1998 年 Brin 與 Page 把「隨機上網的人」的穩態分佈當成網頁的重要性分數，這就是 PageRank。在它之前，搜尋引擎主要看關鍵字出現的頻率，很容易被灌關鍵字的頁面操弄；PageRank 改看「誰連到你」，而且被重要的網頁連到更值錢。
- **一個證明工具**：Ch 30 的 expander 用隨機遊走節省隨機位元；Ch 28 的 Cheeger 不等式在這裡變成「瓶頸 ⇒ 混合慢」。

Markov chain 本身的歷史更早：A. A. Markov 在 1906 年前後研究「下一步只依賴現在」的隨機序列，原本是想證明大數法則不需要獨立性。一個世紀後，它成了 CS 最常用的機率模型之一。

## 先建立直覺

把一滴墨水滴在圖的一個點上。每一秒，每個點上的墨水平均分給它的鄰居。一開始墨水全集中在起點；過了很久，墨水會攤開成一個不再變化的分佈——**穩態**。

```text
t = 0          t = 1           t = 2             ...    t = ∞
  ●              ◐               ◔                       度數大的點墨水多
 / \            / \             / \                      （有更多條路流進來）
○   ○          ◑   ◑           ◑   ◑
```

三個問題貫穿全章：

1. **穩態長什麼樣？** 墨水會不會平均分佈？不會——度數大的點有更多入口，累積更多。
2. **一定會收斂嗎？** 不一定。圖若是二分的，墨水每一步都在兩側之間整批搬家，永遠在振盪。
3. **收斂多快？** 如果圖中間有一個窄頸（兩團用一條邊連起來），墨水要很久才擠得過去。Ch 28 的 λ_2 量的正是這個窄頸。

## Markov chain 的最小語言

**Markov chain**：狀態集合 V（這裡是圖的點），轉移矩陣 P，`P[u][v]` 是「現在在 u、下一步到 v」的機率。每一列加總為 1（**列隨機矩陣**，row-stochastic）。若 `p_t` 是時間 t 的分佈（**列向量**，寫在左邊），則 `p_{t+1} = p_t P`，所以 `p_t = p_0 P^t`。

**穩態分佈**（stationary distribution）π：滿足 `π P = π`、`π ≥ 0`、`Σ π = 1`。

**圖上的簡單隨機遊走**：`P = D^{−1} A`，也就是 `P[u][v] = 1/deg(u)` 若 uv 是邊。

**定理 29.1**：連通圖上簡單隨機遊走的穩態分佈是 `π(v) = deg(v) / (2|E|)`。

**證明**：`(π P)(v) = Σ_u π(u) P[u][v] = Σ_{u~v} (deg(u)/2|E|) · (1/deg(u)) = Σ_{u~v} 1/(2|E|) = deg(v)/(2|E|) = π(v)`。又 `Σ_v deg(v) = 2|E|`（handshake），所以 π 是機率分佈。∎

一個更強、後面一直會用的性質是**可逆性**（detailed balance）：`π(u) P[u][v] = π(v) P[v][u]`，兩邊都等於 `1/(2|E|)`（若 uv 是邊）。直覺：穩態下「每條邊每個方向上的流量」都相等。

唯一性：連通圖上穩態分佈唯一（這是 Perron–Frobenius 的推論，Ch 27「進階」一節）。下面用模擬驗證：

```python
import random
from collections import Counter

import networkx as nx
import numpy as np

# 小圖：一個三角形 {0,1,2}，2 再接一條路 2-3-4，外加 4-1 讓它不是二分圖
adj = {0: {1, 2}, 1: {0, 2, 4}, 2: {0, 1, 3}, 3: {2, 4}, 4: {3, 1}}
nodes = sorted(adj)
n, m = len(nodes), sum(len(adj[v]) for v in adj) // 2
P = np.zeros((n, n))
for u in adj:
    for v in adj[u]:
        P[u, v] = 1 / len(adj[u])          # 從 u 均勻選一個鄰居

pi = np.array([len(adj[v]) / (2 * m) for v in nodes])
print("row sums of P :", P.sum(axis=1))
print("pi            :", np.round(pi, 4))
print("pi P == pi ?  ", np.allclose(pi @ P, pi))

# 模擬一條很長的 walk，看每個點的停留比例
rng = random.Random(2024)
v, steps, visits = 0, 200_000, Counter()
for _ in range(steps):
    v = rng.choice(sorted(adj[v]))
    visits[v] += 1
freq = np.array([visits[u] / steps for u in nodes])
print("empirical freq:", np.round(freq, 4))
print("max |freq - pi| =", round(float(np.max(np.abs(freq - pi))), 4))
```

輸出（seed = 2024）：

```text
row sums of P : [1. 1. 1. 1. 1.]
pi            : [0.1667 0.25   0.25   0.1667 0.1667]
pi P == pi ?   True
empirical freq: [0.1666 0.2488 0.2507 0.1673 0.1666]
max |freq - pi| = 0.0012
```

`rng.choice(sorted(adj[v]))` 裡的 `sorted` 不是多餘的：Python 的 set 迭代順序對整數是穩定的，但對字串會受 hash randomization 影響（`PYTHONHASHSEED`），不排序的話「固定 seed」也不能保證可重現。20 萬步的經驗頻率與 π 相差 0.0012，符合 `O(1/√steps)` 的抽樣誤差量級（不是嚴格界，因為 walk 的樣本彼此相關）。

## 底層機制：為什麼收斂、收斂多快

### 把 P 對稱化

P 一般不對稱（`P[u][v] = 1/deg(u)` 與 `P[v][u] = 1/deg(v)` 不同），不能直接用 Ch 27 的譜定理。技巧是相似變換：

```text
S = D^{1/2} P D^{−1/2} = D^{−1/2} A D^{−1/2}
```

S 是對稱的，而且和 P 相似（`P = D^{−1/2} S D^{1/2}`），所以特徵值相同。S 正是 Ch 28 正規化 Laplacian 的另一半：`N = I − S`。於是：

- P 的特徵值是實數 `1 = μ_1 ≥ μ_2 ≥ ... ≥ μ_n ≥ −1`（因為 N 的特徵值在 `[0, 2]`）。
- `μ_1 = 1` 的 S-特徵向量是 `φ_1(v) = √(deg(v)/2|E|)`，對應 P 的穩態 π。
- 連通圖 `μ_2 < 1`（Ch 28 定理 28.2 的正規化版本）；`μ_n = −1` 若且唯若圖是二分圖（Ch 27 定理 27.5 的連通版本）。

令 `λ* = max(|μ_2|, |μ_n|)`，叫做 P 的**第二大特徵值絕對值**；`1 − λ*` 叫**絕對 spectral gap**。

### 收斂定理與完整證明

**定理 29.2**：連通圖上從 u 出發的隨機遊走，對每個點 v，

```text
|p_t(v) − π(v)| ≤ √(deg(v)/deg(u)) · (λ*)^t
```

**證明**：令 `q_t = p_t D^{−1/2}`（逐分量除以 `√deg`）。則 `q_{t+1} = p_t P D^{−1/2} = p_t D^{−1/2} S = q_t S`，所以 `q_t = q_0 S^t`。把 `q_0` 用 S 的正交歸一特徵基 `φ_1, ..., φ_n` 展開：`q_0 = Σ c_i φ_i`，則 `q_t = Σ c_i μ_i^t φ_i`。

- **第一項就是穩態**：`c_1 = q_0 · φ_1 = (1/√deg(u)) · √(deg(u)/2|E|) = 1/√(2|E|)`，所以 `c_1 φ_1(v) = √deg(v) / (2|E|)`；乘回 `√deg(v)` 得 `deg(v)/2|E| = π(v)`。
- **其他項指數衰減**：令 `r_t = q_t − c_1 φ_1 = Σ_{i≥2} c_i μ_i^t φ_i`。由正交性，`‖r_t‖^2 = Σ_{i≥2} c_i^2 μ_i^{2t} ≤ (λ*)^{2t} Σ_{i≥2} c_i^2 ≤ (λ*)^{2t} ‖q_0‖^2 = (λ*)^{2t} / deg(u)`。
- **換回 p**：`p_t(v) − π(v) = √deg(v) · r_t(v)`，而 `|r_t(v)| ≤ ‖r_t‖`。合起來得證。∎

推論：若 `λ* < 1`（連通且非二分），`p_t → π`，速度是幾何級數。若圖是二分的，`λ* = 1`，定理什麼也沒說——而且真的不收斂。

### 二分圖與 lazy walk

標準修法是 **lazy walk**：每一步先擲硬幣，一半機率留在原地，一半機率照常走。轉移矩陣 `P' = (I + P)/2`，特徵值 `(1 + μ_i)/2 ∈ [0, 1]`，負特徵值消失，`λ* = (1 + μ_2)/2`。穩態不變（`π P' = (π + π P)/2 = π`）。代價是慢了大約一倍。

```python
import networkx as nx
import numpy as np

# 二分圖上的「非 lazy」walk：C10，從 0 出發
G = nx.cycle_graph(10)
A = nx.to_numpy_array(G)
P = A / A.sum(axis=1)[:, None]
pi = np.full(10, 0.1)
for name, M in [("simple", P), ("lazy", 0.5 * np.eye(10) + 0.5 * P)]:
    p = np.zeros(10)
    p[0] = 1
    tv = []
    for t in range(1, 201):
        p = p @ M
        if t in (49, 50, 199, 200):
            tv.append(f"t={t}: TV={0.5 * np.abs(p - pi).sum():.4f}")
    print(f"{name:6s}", "  ".join(tv))
```

輸出：

```text
simple t=49: TV=0.5000  t=50: TV=0.5000  t=199: TV=0.5000  t=200: TV=0.5000
lazy   t=49: TV=0.0047  t=50: TV=0.0043  t=199: TV=0.0000  t=200: TV=0.0000
```

TV 是**全變差距離**（total variation distance）：`TV(p, π) = (1/2) Σ_v |p(v) − π(v)|`，等於「任一事件在兩個分佈下機率差的最大值」。簡單 walk 在 C10 上走奇數步一定在奇數點、偶數步一定在偶數點，所以 TV 永遠是 0.5；lazy walk 正常收斂。

### Mixing time 與 spectral gap

**mixing time**：`t_mix(ε) = min{ t : 對所有起點 u，TV(p_t^{(u)}, π) ≤ ε }`，慣例取 ε = 1/4。**relaxation time**：`t_rel = 1/γ`，γ 是 spectral gap（對 lazy walk，`γ = 1 − μ_2'`，μ_2' 是 P' 的第二大特徵值）。

對可逆的 lazy chain，Levin–Peres 的教科書（第 12 章）證明了兩側界：

```text
(t_rel − 1) · ln(1/(2ε))  ≤  t_mix(ε)  ≤  t_rel · ln(1/(ε π_min))
```

上界本質上就是定理 29.2 對 v 加總後取對數；下界說「spectral gap 小，就一定混合慢」，用的是「把 μ_2 的特徵向量當成一個衰減很慢的觀測量」。**這兩個界本章不證**，但我們實測：

```python
import networkx as nx
import numpy as np


def lazy_walk(G):
    A = nx.to_numpy_array(G, nodelist=sorted(G))
    d = A.sum(axis=1)
    P = 0.5 * np.eye(len(d)) + 0.5 * A / d[:, None]     # 一半機率留在原地
    return P, d / d.sum()


def mixing_time(P, pi, eps=0.25, tmax=200_000):
    """最壞起點下，TV 距離第一次 <= eps 的 t（對所有起點同時演化）。"""
    Q = np.eye(len(pi))
    for t in range(1, tmax + 1):
        Q = Q @ P
        if 0.5 * np.abs(Q - pi).sum(axis=1).max() <= eps:
            return t
    return None


def spectral_gap(G):
    """lazy walk 的 gap = 1 - (第二大特徵值)；用對稱化的 D^{-1/2} A D^{-1/2} 計算。"""
    A = nx.to_numpy_array(G, nodelist=sorted(G))
    d = A.sum(axis=1)
    S = A / np.sqrt(np.outer(d, d))
    mu = np.sort(np.linalg.eigvalsh(S))[::-1]
    return 1 - (0.5 + 0.5 * mu[1])


graphs = {
    "complete K64": nx.complete_graph(64),
    "3-regular n=64 s=1": nx.random_regular_graph(3, 64, seed=1),
    "6-regular n=64 s=1": nx.random_regular_graph(6, 64, seed=1),
    "hypercube Q6": nx.convert_node_labels_to_integers(nx.hypercube_graph(6)),
    "cycle C64": nx.cycle_graph(64),
    "barbell K32-K32": nx.barbell_graph(32, 0),
}
eps = 0.25
print(f"{'graph':20s} {'gap':>9s} {'t_rel':>8s} {'lower':>8s} {'t_mix':>6s} {'upper':>8s}")
for name, G in graphs.items():
    P, pi = lazy_walk(G)
    gap = spectral_gap(G)
    t_rel = 1 / gap
    lower = (t_rel - 1) * np.log(1 / (2 * eps))
    upper = t_rel * np.log(1 / (eps * pi.min()))
    t = mixing_time(P, pi, eps)
    assert lower <= t <= upper
    print(f"{name:20s} {gap:9.5f} {t_rel:8.1f} {lower:8.1f} {t:6d} {upper:8.1f}")
```

輸出（本機不到 1 秒）：

```text
graph                      gap    t_rel    lower  t_mix    upper
complete K64           0.50794      2.0      0.7      2     10.9
3-regular n=64 s=1     0.04096     24.4     16.2     30    135.4
6-regular n=64 s=1     0.16914      5.9      3.4      8     32.8
hypercube Q6           0.16667      6.0      3.5      8     33.3
cycle C64              0.00241    415.3    287.2    389   2303.2
barbell K32-K32        0.00095   1053.1    729.3    732   5840.8
```

所有 64 點的圖，`t_mix` 從 2 到 732，差了 366 倍，而 spectral gap 精準地預測了排序：

- **K64**：一步就幾乎均勻。
- **隨機正則圖與超立方體**：gap 是常數（不隨 n 變小），`t_mix = O(log n)`。這就是 Ch 30 expander 的定義性質。注意 n = 64 時 6-正則隨機圖和 Q6 幾乎一樣；差別要在 n 變大時才出現——Q_d 的度數 d = log₂ n 跟著長，而隨機 6-正則圖的度數固定是 6。
- **圈 C64**：gap ≈ π²/n²，`t_mix = Θ(n²)`。這是擴散的典型行為：走 t 步只能離開起點約 √t 的距離，要覆蓋 n 需要 n² 步。
- **barbell**：兩團 K32 用一條邊連起來，gap 最小。墨水在一團裡瞬間攤平，但要擠過那條橋，平均要等很多步。

最後一點是 Ch 28 Cheeger 不等式的動態版本：lazy walk 的 gap 是 `ν_2/2`，而 `ν_2 ≤ 2φ(G)`，所以**有窄頸（φ 小）⇒ gap 小 ⇒ 混合慢**；反方向 `ν_2 ≥ φ²/2` 說**沒有窄頸 ⇒ 混合快**。

## PageRank：給有向圖一個穩態

網頁連結是有向圖，而且有兩種病：

- **dangling node**：沒有出邊的頁面（PDF、圖片、死路）。隨機上網的人走到這裡就卡住，P 的那一列全是 0，不再是隨機矩陣。
- **陷阱與週期**：一群頁面只互相連、不往外連，walk 進去就出不來；或者整個結構有週期性，分佈不收斂。

Brin 與 Page 的修法是加一個**傳送**（teleportation）：每一步以機率 α 照連結走，以機率 1−α 跳到一個均勻隨機的頁面；走到 dangling node 時一律均勻亂跳。他們的論文寫「通常把 d 設為 0.85」，後來的文獻改叫 α。寫成矩陣（**Google matrix**）：

```text
P̂ = P，但把 dangling node 那一列換成 (1/n, ..., 1/n)
G = α P̂ + (1 − α) (1/n) J          （J 是全 1 矩陣）
PageRank x = G 的穩態分佈：x G = x
```

G 的每個元素都 ≥ `(1−α)/n > 0`，所以它是一個不可約、非週期的 Markov chain，由 Perron–Frobenius，穩態存在且唯一。

**定理 29.3（冪迭代的收斂速度）**：對任意兩個機率分佈 x、y，`‖xG − yG‖_1 ≤ α ‖x − y‖_1`。因此從任何起點做冪迭代，`‖x_t − x‖_1 ≤ 2 α^t`。

**證明**：令 `z = x − y`，`Σ z_v = 0`。`zG = α z P̂ + (1−α)(1/n)(Σ z_v) 1 = α z P̂`——傳送項因為 z 的總和為 0 而消失。P̂ 是隨機矩陣，`‖z P̂‖_1 = Σ_w |Σ_u z_u P̂[u][w]| ≤ Σ_u |z_u| Σ_w P̂[u][w] = ‖z‖_1`。所以 `‖zG‖_1 ≤ α ‖z‖_1`。迭代 t 次，再用 `‖x_0 − x‖_1 ≤ 2`。∎

這個證明說明了 α 的雙重角色：**α 越小收斂越快，但 PageRank 越接近均勻分佈、越不反映連結結構**。0.85 是兩者的折衷：`0.85^t ≤ 1e-12` 需要約 170 次迭代，而實務上的誤差比這個最壞界衰減得更快。

### 自刻冪迭代，對拍 nx.pagerank

```python
import networkx as nx
import numpy as np


def pagerank(succ, alpha=0.85, tol=1e-12, max_iter=1000):
    """冪迭代 PageRank。succ: dict[node, set[node]]（有向圖）。
    dangling node（沒有出邊）的機率質量均勻灑回所有點，與 nx.pagerank 預設一致。"""
    nodes = sorted(succ)
    n = len(nodes)
    x = {v: 1 / n for v in nodes}
    history = []
    for it in range(1, max_iter + 1):
        dangling_mass = alpha * sum(x[v] for v in nodes if not succ[v])
        new = {v: (1 - alpha) / n + dangling_mass / n for v in nodes}
        for u in nodes:
            if succ[u]:
                share = alpha * x[u] / len(succ[u])
                for w in succ[u]:
                    new[w] += share
        err = sum(abs(new[v] - x[v]) for v in nodes)       # L1 變化量
        history.append(err)
        x = new
        if err < tol:
            return x, it, history
    raise RuntimeError("did not converge")


# 6 個網頁：5 沒有出邊（dangling），{3,4} 互相指形成一個小陷阱
succ = {0: {1, 2}, 1: {2}, 2: {0}, 3: {4}, 4: {3, 5}, 5: set()}
x, iters, hist = pagerank(succ)
D = nx.DiGraph([(u, w) for u in succ for w in succ[u]])
D.add_nodes_from(succ)
ref = nx.pagerank(D, alpha=0.85, tol=1e-12, max_iter=1000)
print("iterations:", iters)
for v in sorted(succ):
    print(f"  page {v}: mine={x[v]:.6f}  networkx={ref[v]:.6f}")
print("max diff =", max(abs(x[v] - ref[v]) for v in succ))
print("sum =", round(sum(x.values()), 12))
ratios = [hist[i + 1] / hist[i] for i in range(5, 10)]
print("error ratio per iteration:", np.round(ratios, 4))

# 較大的隨機有向圖對拍（seed 固定）
R = nx.gnp_random_graph(500, 0.01, directed=True, seed=42)
succ_r = {u: set(R.successors(u)) for u in R}
xr, it_r, _ = pagerank(succ_r)
refr = nx.pagerank(R, alpha=0.85, tol=1e-12, max_iter=1000)
print(f"G(500,0.01) directed: dangling={sum(1 for u in succ_r if not succ_r[u])}, iters={it_r}, "
      f"max diff = {max(abs(xr[v] - refr[v]) for v in R):.2e}")
```

輸出：

```text
iterations: 81
  page 0: mine=0.283491  networkx=0.283491
  page 1: mine=0.157036  networkx=0.157036
  page 2: mine=0.290517  networkx=0.290517
  page 3: mine=0.081545  networkx=0.081545
  page 4: mine=0.105866  networkx=0.105866
  page 5: mine=0.081545  networkx=0.081545
max diff = 2.3863133691293115e-12
sum = 1.0
error ratio per iteration: [0.727  0.5781 0.7858 0.6741 0.7496]
G(500,0.01) directed: dangling=1, iters=33, max diff = 3.94e-12
```

幾個細節：

- **頁 2 分數最高**：它被 0 和 1 兩頁連到，而且 1 把全部票都投給它。頁 3 與頁 5 分數相同，因為兩者唯一的入口都是頁 4 的一半出邊（加上相同的傳送與 dangling 份額）。
- **誤差比 ≤ 0.85**：每次迭代 L1 變化量縮小 0.58–0.79 倍，比定理 29.3 的最壞界 α = 0.85 更好。真正的速率由 G 的第二大特徵值決定，而它的絕對值 ≤ α。
- **為什麼要傳 `tol=1e-12`**：`nx.pagerank` 預設 `tol=1e-6`、`max_iter=100`，而且停止條件是 `err < N * tol`（N 是點數，見 NetworkX 3.7 原始碼 `pagerank_alg.py`）。對 100 萬個點的圖，預設的停止門檻是 L1 誤差 1——幾乎等於沒收斂。對拍時兩邊都要用夠嚴的容差。
- **dangling 處理**：`nx.pagerank` 的 `dangling=None` 時，dangling node 的質量依 personalization 向量分配，沒給 personalization 就是均勻分配，和我們的實作一致。

### 失敗示範：拿掉傳送

```python
import numpy as np


def power(P, x0, alpha, steps):
    n = len(x0)
    x = x0.copy()
    for _ in range(steps):
        x = alpha * x @ P + (1 - alpha) / n
    return x


# (a) 陷阱：0->1->2->0 是封閉的，3->0、4->3；沒有 teleport 時 3、4 的分數歸零
P = np.zeros((5, 5))
for u, w in [(0, 1), (1, 2), (2, 0), (3, 0), (4, 3)]:
    P[u, w] = 1
x0 = np.full(5, 0.2)
print("(a) alpha=1.00:", np.round(power(P, x0, 1.00, 300), 4))
print("    alpha=0.85:", np.round(power(P, x0, 0.85, 300), 4))

# (b) 週期：有向 3-cycle，從「全部質量在 0」出發
C = np.zeros((3, 3))
for u, w in [(0, 1), (1, 2), (2, 0)]:
    C[u, w] = 1
x0 = np.array([1.0, 0.0, 0.0])
for t in (300, 301, 302):
    print(f"(b) alpha=1.00 t={t}:", power(C, x0, 1.00, t), "  alpha=0.85:", np.round(power(C, x0, 0.85, t), 4))
```

輸出：

```text
(a) alpha=1.00: [0.2 0.4 0.4 0.  0. ]
    alpha=0.85: [0.3223 0.3039 0.2883 0.0555 0.03  ]
(b) alpha=1.00 t=300: [1. 0. 0.]   alpha=0.85: [0.3333 0.3333 0.3333]
(b) alpha=1.00 t=301: [0. 1. 0.]   alpha=0.85: [0.3333 0.3333 0.3333]
(b) alpha=1.00 t=302: [0. 0. 1.]   alpha=0.85: [0.3333 0.3333 0.3333]
```

(a) 沒有傳送時，頁 3、4 的分數變成 0——它們確實有被連（4→3），但質量全被吸進 `{0,1,2}` 這個封閉的強連通分量（Ch 13 的 sink SCC）。更糟的是 `[0.2, 0.4, 0.4]` 甚至不是穩態：質量在三角形裡繞圈，t = 301 時會變成另一個排列。(b) 純週期的 3-cycle 上，分佈永遠在轉。加上 α = 0.85 的傳送後兩個問題同時消失。

## 對比與取捨

| 計算 PageRank 的方法 | 每次成本 | 收斂 | 適用 |
| --- | --- | --- | --- |
| 冪迭代（本章） | 一次稀疏矩陣乘向量 `O(m)` | 誤差 `≤ 2α^t` | 網頁規模的圖；容易平行化、容易增量更新 |
| 解線性系統 `x(I − αP̂) = (1−α)/n · 1` | 稠密 `O(n^3)`；稀疏迭代法每次 `O(m)` | Krylov 方法（GMRES 等）在 α 接近 1 時常比冪迭代快 | 中型圖、需要高精度 |
| 特徵值求解器（ARPACK） | 每次 `O(m)` 加上正交化 | 依特徵值間隙 | 想同時看其他特徵向量時 |
| 蒙地卡羅 walk 取樣 | 每條 walk `O(1/(1−α))` 步 | 誤差 `O(1/√樣本數)` | 只要少數點的 personalized PageRank 時 |

**為什麼 Google 不直接用入度？** 入度只數「有多少頁連到你」，不管連你的是誰；灌一萬個垃圾頁面互連就能拉高。PageRank 是遞迴定義的：被高分頁面連到才值錢。它也不是無懈可擊——link farm 仍然能操弄它，這導致後來 TrustRank、personalized PageRank 一類以「可信種子集合」為傳送目標的變體。

**為什麼用 lazy walk 而不用傳送？** 兩者解決不同的病。lazy walk 處理週期性，不處理不可約性（圖不連通它也救不了）；傳送同時處理兩者，但會改變穩態——PageRank 不是原圖隨機遊走的穩態，是一個「被均勻分佈稀釋過」的版本。

## 踩雷集錦

1. **列向量 vs 行向量**：本章 `p_{t+1} = p_t P`，P 是列隨機。很多線代教材用行隨機矩陣 `M = P^T` 與 `p_{t+1} = M p_t`。混用時穩態會變成「左特徵向量 vs 右特徵向量」的錯：`np.linalg.eig(P)` 給的是右特徵向量，P 的右特徵向量對 1 是全 1 向量，不是 π。要嘛 `eig(P.T)`，要嘛照本章對稱化。
2. **以為穩態是均勻分佈**：只有正則圖如此。社交網路上，隨機遊走取樣會偏向高度數的使用者——用 walk 估計「平均好友數」會高估，要用 `1/deg` 做重要性加權修正。
3. **忘記 dangling node**：直接用 `A / out_degree` 會除以 0，或者讓那一列全 0、機率質量每一步漏掉一點，最後所有分數趨近 0。
4. **相信 `nx.pagerank` 的預設容差**：`err < N * tol` 對大圖非常寬鬆；文件本身也警告 `N * tol ≥ 2` 時可能一步就「收斂」。
5. **用 `t_mix` 的上界估實際執行時間**：表中 C64 的上界 2303 是實測 389 的 6 倍。上界裡的 `ln(1/π_min)` 項在很多圖上是鬆的；工程上要估時間，直接在小規模上實測再外推。

## 進階：再往深一層

**Personalized PageRank 與局部分群**：把傳送目標從均勻分佈換成一個種子點 s，得到「從 s 的角度看誰重要」。Andersen–Chung–Lang 的 push 演算法只觸碰種子附近的點，時間與圖的大小無關，再對結果做 sweep cut（Ch 28），能找到 conductance 有保證的局部群集——這是 Ch 28 Cheeger 不等式的局部版本，Spielman 書稿 Chapter 22「Local Graph Clustering」有完整推導。資安上的對應是：從一個已知被入侵的帳號做 personalized PageRank，找出在權限圖上「離它最近」的資產（Ch 39 攻擊圖會用到同類想法）。

**Hitting time、cover time 與電阻**：從 u 第一次走到 v 的期望步數 `H(u,v)` 和 Ch 28 的有效電阻有精確關係：commute time `H(u,v) + H(v,u) = 2|E| · R_eff(u,v)`。由此可證任何連通圖的 cover time（走遍所有點的期望時間）是 `O(|V| · |E|)`；這是「隨機遊走在對數空間判斷無向圖連通性」這類結果的起點，最終被 Reingold 的確定性演算法超越（Ch 30）。

**MCMC**：想從一個難以直接抽樣的分佈 π 抽樣（例如所有合法著色、所有完美匹配），就設計一個以 π 為穩態的 Markov chain（Metropolis–Hastings、Glauber dynamics），讓它跑過 mixing time。整個「近似計數」理論（Jerrum–Sinclair 的 permanent 近似）就是在證明特定 chain 的 conductance 夠大。

## 動手練習

1. 把上面的 mixing time 程式改成畫圖：對 `C_n`（n = 8..128）與隨機 3-正則圖（同 n，seed = 1）畫 `t_mix` 對 n 的 log-log 圖。你會看到斜率約 2 與約 0（對數成長）兩種行為。
2. 證明 lazy walk 的 gap 與正規化 Laplacian 的關係 `γ = ν_2/2`，再結合 Ch 28 的 Cheeger 不等式寫出 `φ²/4 ≤ γ ≤ φ`。
3. 在 `pagerank` 加一個 `personalization` 參數（傳送目標分佈），和 `nx.pagerank(..., personalization=...)` 對拍。
4. 故意弄壞：把 dangling 處理刪掉（dangling 的質量直接丟掉），在 6 頁的例子上跑，印出每次迭代後分數總和，看它怎麼漏光。

## 本章重點整理

- 隨機遊走 `P = D^{−1}A`，穩態 `π(v) = deg(v)/2|E|`，滿足 detailed balance。
- P 與對稱的 `D^{−1/2} A D^{−1/2}` 相似；收斂速度由 `λ* = max(|μ_2|, |μ_n|)` 控制，`|p_t(v) − π(v)| ≤ √(deg v / deg u) λ*^t`。
- 二分圖 `μ_n = −1`，不收斂；lazy walk 修好週期性。
- `t_mix` 夾在 `(t_rel − 1) ln(1/2ε)` 與 `t_rel ln(1/(ε π_min))` 之間；有窄頸 ⇒ gap 小 ⇒ 混合慢。
- PageRank = 加了傳送的 walk 的穩態；冪迭代是 L1 上的 α-壓縮映射，誤差 `≤ 2α^t`。

## 自我檢核

- [ ] 不看筆記，從 `q_t = p_t D^{−1/2}` 出發重建定理 29.2 的證明，並說出哪一步用到了圖是連通的。
- [ ] 同事說「我用 random walk 在社交圖上取樣了 1 萬個使用者，平均好友數是 230」——你會怎麼指出問題、怎麼修正？
- [ ] 能解釋為什麼傳送項在定理 29.3 的證明裡會消失，以及這對 α 的選擇意味著什麼。
- [ ] 能說出 lazy walk 與傳送分別修好了哪一種病，各自的代價是什麼。
- [ ] 想一個你工作中的有向圖（服務呼叫、權限委派、程式呼叫圖）：它有沒有 sink SCC？在上面跑沒有傳送的 PageRank 會發生什麼？

## 延伸閱讀

### 書籍

- **[Markov Chains and Mixing Times（第 2 版）](https://pages.uoregon.edu/dlevin/MARKOV/)** — David A. Levin、Yuval Peres，with contributions by Elizabeth L. Wilmer（AMS，2017）
  - **這本書的定位**：mixing time 的標準教科書，作者提供免費 PDF（頁面上的 `mcmt2e.pdf`）。
  - **讀哪幾章**：Chapter 1（Markov chain 基礎與穩態）、Chapter 4（TV 距離與 mixing time 定義）、Chapter 12「Eigenvalues」（本章引用的兩側界就在這章）；Chapter 5 的 coupling 是另一套證明收斂的方法，值得對照。
  - **前提知識**：大學機率（條件機率、期望值）與本章。
- **[Spectral and Algebraic Graph Theory](https://www.cs.yale.edu/homes/spielman/sagt/)** — Daniel A. Spielman（Yale，書稿 2025-04-02 版）
  - **讀哪幾章**：Chapter 10「Random Walks on Graphs」（walk 矩陣的譜、穩態、收斂速度，和本章證明幾乎同一條路）；Chapter 22「Local Graph Clustering」（personalized PageRank 與局部 sweep）。
  - **前提知識**：Ch 27–28。
- **《Google's PageRank and Beyond: The Science of Search Engine Rankings》** — Amy N. Langville、Carl D. Meyer（Princeton University Press，2006）
  - **這本書的定位**：PageRank 的數學與計算細節最完整的一本；dangling node、α 的敏感度、冪迭代以外的解法都有專章。2006 年出版，搜尋引擎的實務早已改變，但數學部分仍然適用。
  - **讀哪幾章**：開頭幾章（從隨機上網者到 Google matrix 的構造），以及講冪迭代收斂速度與 α 敏感度的章節。
  - **前提知識**：線性代數。

### 論文

- **[The Anatomy of a Large-Scale Hypertextual Web Search Engine](https://www.ics.uci.edu/~djp3/classes/2008_01_01_INF141/Materials/brin1998.pdf)** — Sergey Brin、Lawrence Page，Computer Networks and ISDN Systems 30（1998），107–117
  - **核心貢獻**：Google 原型系統的架構論文；PageRank 的定義與「d 通常設為 0.85」出自 Section 2.1。
  - **讀哪裡**：Section 2.1（PageRank 與隨機上網者的直覺）；其餘講爬蟲與索引的部分是系統歷史，可選讀。連結是 UC Irvine 課程網頁上的掃描。
  - **和本章的關聯**：本章 Google matrix 的原始動機。
- **[The PageRank Citation Ranking: Bringing Order to the Web](http://ilpubs.stanford.edu:8090/422/)** — Lawrence Page、Sergey Brin、Rajeev Motwani、Terry Winograd，Stanford InfoLab Technical Report 1999-66（1999）
  - **核心貢獻**：PageRank 的完整技術報告，包括 rank sink（本章失敗示範 a）的討論與 personalized PageRank 的想法。
  - **讀哪裡**：前半講 PageRank 定義、rank sink 與 dangling links 的小節；後半的實作與實驗可選讀。
  - **和本章的關聯**：NetworkX 的 `pagerank` 文件把這篇列為參考文獻。
- **Random walks on graphs: a survey** — László Lovász，收錄於 *Combinatorics, Paul Erdős is Eighty, Vol. 2*（Bolyai Society Mathematical Studies，1993），1–46
  - **核心貢獻**：隨機遊走與譜、電阻、hitting/cover time 關係的經典綜述。
  - **讀哪裡**：講特徵值與 mixing 的部分，以及講電阻與 hitting/commute time 的部分；全文約 46 頁，可依本章「進階」的兩個主題挑讀。
  - **和本章的關聯**：「進階」一節 commute time 與有效電阻的公式出自這類結果。我們沒找到作者本人託管的穩定網址，請以標題搜尋。

隨機遊走混合得快的圖，正是「沒有任何窄頸」的圖。下一章把這個性質當成定義，問：度數固定時，一張圖最多能多「沒有窄頸」？這種圖叫 expander，它們在錯誤更正碼、去隨機化與資料中心網路裡到處出現。

→ [Ch 30 Expander graph](./30-expander-graphs.md)
