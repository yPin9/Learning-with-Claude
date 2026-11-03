# Ch 36 — Treewidth 與樹分解

> **目標**：能寫出樹分解的三條定義並檢查一個分解合不合法；證明「treewidth = 最佳消去序的寬度 = 最小 chordal 補圖的團數減一」；會用 NetworkX 的 treewidth heuristic 並知道它在哪裡失手；親手在樹分解上做 DP 解最大獨立集，與暴力對拍；能陳述 Courcelle 定理並說清楚它的「線性時間」藏了什麼。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11（AMD Ryzen 7 5700G）。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。跑範例前設 `PYTHONUTF8=1`。

## 為什麼需要這個？

[Ch 34](./34-np-complete-reductions.md) 證明了最大獨立集、3-著色、Hamilton cycle 都是 NP-complete。可是在**樹**上，最大獨立集是一個教科書等級的 DP：每個點記「選我」和「不選我」兩個值，由葉往根合併，線性時間。樹上 3-著色更是永遠可行（樹是二分圖）。

所以「難」不是問題本身的性質，而是問題加上**圖的結構**。樹之所以好做，是因為拿掉任何一個點，圖就裂成互不相干的幾塊，子問題之間只透過那一個點溝通。Treewidth 把這個性質一般化：如果圖可以被一串「小分隔集」切開，子問題之間只透過分隔集溝通，DP 的狀態數就只和分隔集大小有關，和整張圖的大小無關。

這個想法有兩條源頭。一條是 1970–80 年代的資料庫與機率推論（junction tree、Bayesian network 的變數消去），另一條是 Robertson 與 Seymour 在圖子式（graph minor）計畫裡引入 tree-width（Graph Minors II，1986）。今天它是參數化演算法（parameterized algorithms）的核心概念：一個問題如果能在 `f(tw) · n` 時間內解，就叫對 treewidth **固定參數可解**（fixed-parameter tractable, FPT）。

對我們的應用也直接相關：Thorup（1998）證明了不含 goto 的結構化程式，其控制流圖（CFG）的 treewidth 至多 6。這意味著 CFG 上很多一般圖上 NP-hard 的問題（例如某些暫存器分配的變體）在實際程式上是可解的。

## 先建立直覺

想像你要把一張圖「沿著一棵樹攤開」。樹的每個節點放一個袋子（bag），袋子裡裝幾個原圖的點，規則是：

```text
原圖（6-cycle）                 樹分解（每個袋子 3 點，寬度 2）

  0 ─── 1                        {0,1,5}
  │     │                           │
  5     2                        {1,2,5}
  │     │                           │
  4 ─── 3                        {2,3,5}
                                    │
                                 {3,4,5}
                                    │
                                  {4,5}
                                    │
                                   {5}
```

三條規則（後面寫成程式）：(1) 每個點至少在一個袋子裡；(2) 每條邊的兩端至少同時出現在一個袋子裡；(3) 包含同一個點的那些袋子，在樹上必須連成一塊。第 (3) 條是靈魂：它保證「點 5 的資訊」沿著樹一路傳下去，不會在某處斷掉又在別處冒出來。

寬度（width）是最大袋子的大小減一。**Treewidth** `tw(G)` 是所有樹分解裡最小的寬度。減一是為了讓樹的 treewidth 恰好是 1（每條邊一個袋子，袋子大小 2）。

```text
圖類             treewidth
--------------------------------
樹（至少一條邊）   1
cycle            2
k×n 格子          min(k, n)
K_n              n − 1
```

> 如果你對 chordal graph 與完美消去序（PEO）還不熟，先回看 [Ch 16 — Chordal graph 與完美消去序](./16-chordal-graphs.md)。這章的核心定理就是把樹分解和 PEO 接起來。

## 樹分解的定義與檢查器

**定義**：圖 `G = (V, E)` 的樹分解是一對 `(T, {X_t})`，T 是一棵樹，每個樹節點 t 有一個袋子 `X_t ⊆ V`，滿足：

1. `∪ X_t = V`
2. 對每條邊 `uv ∈ E`，存在 t 使 `{u, v} ⊆ X_t`
3. 對每個點 v，`{t : v ∈ X_t}` 在 T 上導出連通子樹

最直接的構造方式是**消去序（elimination ordering）**：依某個順序一個一個刪點，刪 v 時把它當下的鄰居補成團（加的邊叫 fill-in），袋子 `X_v = {v} ∪ 當下的鄰居`，並把袋子 v 掛到「它的鄰居中最早被刪的那個」底下。

```python
# td_basics.py
import itertools
import networkx as nx


def is_tree_decomposition(adj, bags, tree):
    """bags: dict[node_of_T, frozenset]；tree: 鄰接字典，必須是一棵樹。"""
    # (0) T 是樹
    nT = len(tree)
    eT = sum(map(len, tree.values())) // 2
    if eT != nT - 1 or not _connected(tree, set(tree)):
        return False, "T 不是樹"
    # (1) 每個點都在某個袋子裡
    if set().union(*bags.values()) != set(adj):
        return False, "有點沒被任何袋子蓋到"
    # (2) 每條邊的兩端同時出現在某個袋子裡
    for u in adj:
        for v in adj[u]:
            if not any(u in B and v in B for B in bags.values()):
                return False, f"邊 {u}-{v} 沒有袋子同時包含兩端"
    # (3) 包含 v 的袋子在 T 上連通
    for v in adj:
        holders = {t for t, B in bags.items() if v in B}
        if not _connected(tree, holders):
            return False, f"包含 {v} 的袋子在 T 上不連通"
    return True, "ok"


def _connected(adj, nodes):
    nodes = set(nodes)
    if not nodes:
        return True
    start = next(iter(nodes))
    seen, stack = {start}, [start]
    while stack:
        u = stack.pop()
        for w in adj[u]:
            if w in nodes and w not in seen:
                seen.add(w)
                stack.append(w)
    return seen == nodes


def width(bags):
    return max(len(B) for B in bags.values()) - 1


def td_from_order(adj, order):
    """依消去序建樹分解：消去 v 時，袋子 = {v} ∪ 它當下的鄰居，鄰居兩兩補邊（fill-in）。
    回傳 (bags, tree, filled_graph)。"""
    g = {u: set(adj[u]) for u in adj}
    filled = {u: set(adj[u]) for u in adj}
    pos = {v: i for i, v in enumerate(order)}
    bags, parent_of = {}, {}
    for v in order:
        nb = set(g[v])
        bags[v] = frozenset(nb | {v})
        for a, b in itertools.combinations(nb, 2):     # 把鄰居補成團
            g[a].add(b); g[b].add(a)
            filled[a].add(b); filled[b].add(a)
        for w in nb:
            g[w].discard(v)
        del g[v]
        # 袋子 v 掛到「它的鄰居中最早被消去的那個」底下
        if nb:
            parent_of[v] = min(nb, key=pos.get)
    tree = {v: set() for v in order}
    for v, p in parent_of.items():
        tree[v].add(p); tree[p].add(v)
    # 不連通的圖會得到森林：把各棵樹的根串起來（相鄰袋子交集為空也合法）
    roots = [v for v in order if v not in parent_of]
    for a, b in zip(roots, roots[1:]):
        tree[a].add(b); tree[b].add(a)
    return bags, tree, filled


if __name__ == "__main__":
    # 例子：6-cycle 0-1-2-3-4-5-0
    C6 = {i: {(i - 1) % 6, (i + 1) % 6} for i in range(6)}
    bags, tree, filled = td_from_order(C6, [0, 1, 2, 3, 4, 5])
    print("袋子", {t: sorted(B) for t, B in bags.items()})
    print("合法?", is_tree_decomposition(C6, bags, tree), "寬度", width(bags))

    # 故意弄壞：把 3 從袋子 2 拿掉
    broken = dict(bags)
    broken[2] = frozenset(broken[2] - {3})
    print("弄壞後", is_tree_decomposition(C6, broken, tree))

    # fill-in 後的圖是 chordal，且 ω - 1 = 寬度
    G = nx.Graph([(u, v) for u in filled for v in filled[u]])
    print("填完是 chordal?", nx.is_chordal(G), "chordal_graph_treewidth =", nx.chordal_graph_treewidth(G))
```

```text
袋子 {0: [0, 1, 5], 1: [1, 2, 5], 2: [2, 3, 5], 3: [3, 4, 5], 4: [4, 5], 5: [5]}
合法? (True, 'ok') 寬度 2
弄壞後 (False, '邊 2-3 沒有袋子同時包含兩端')
填完是 chordal? True chordal_graph_treewidth = 2
```

刪掉 0 時它的鄰居是 1、5，補邊 1–5；刪 1 時鄰居是 2、5，補 2–5；以此類推。最後一行接到了 Ch 16：fill-in 之後的圖是 chordal，NetworkX 的 `chordal_graph_treewidth`（chordal 圖的 treewidth 等於最大團減一，可以多項式時間算）給出 2。

## 底層機制：treewidth = 最佳消去序 = 最小 chordal 補圖

**定理**：對任意圖 G 與整數 k，以下三者等價：

- (a) G 有寬度 ≤ k 的樹分解；
- (b) 存在消去序，使得每個點被刪時至多有 k 個鄰居（在逐步補邊後的圖上算）；
- (c) G 是某個 chordal 圖 H 的子圖，且 `ω(H) ≤ k + 1`。

**證明**。

**(b) ⇒ (a)**：用 `td_from_order` 的構造，令 `N⁺(v)` 為 v 被刪時的鄰居集合，袋子 `X_v = {v} ∪ N⁺(v)`，大小 ≤ k + 1。驗三條：(1) 每個 v 在自己的袋子裡。(2) 邊 uv 中先被刪的那個（設為 u）被刪時 v 還在、而且是它的鄰居（補邊只會加邊不會刪邊），所以 `{u, v} ⊆ X_u`。(3) 設 p 是 `N⁺(v)` 中最早被刪的點，袋子 v 掛在袋子 p 底下。關鍵觀察：刪 v 時 `N⁺(v)` 被補成團，所以 `N⁺(v) \ {p} ⊆ N⁺(p)`（這些點比 p 晚刪，在 p 被刪時是 p 的鄰居）。於是對任一點 w，包含 w 的袋子是 `X_w` 以及所有 `w ∈ N⁺(v)` 的 `X_v`；從任一個這樣的 `X_v` 沿著 parent 往上走，每一步 w 都還在袋子裡（若 w ≠ p 由上面的包含關係；若 w = p 就已經走到 `X_w`），直到抵達 `X_w`。所以包含 w 的袋子都和 `X_w` 連通。T 是樹：每個袋子（除了每個連通分量最後刪的點）恰有一個 parent，而且 parent 比自己晚刪，不會成環；不連通時把各分量的根串起來，因為不同分量的袋子沒有共同點，(3) 不受影響。

**(a) ⇒ (c)**：把每個袋子補成團得到 H。H 包含 G（規則 2），團數 ≤ k + 1：H 的每個團 C 都完全落在某個袋子裡，這是樹分解的 Helly 性質（每個點對應 T 上一棵子樹，兩兩相交的子樹族必有共同節點），這裡不重證。H 是 chordal：它是「樹的子樹族」的交集圖，Gavril（1974）證明了這類圖恰好就是 chordal 圖；也可以直接用下面的 (c) ⇒ (b) 的論證造出它的 PEO。

**(c) ⇒ (b)**：H chordal，由 [Ch 16](./16-chordal-graphs.md) 它有完美消去序（PEO）：每個點被刪時，它在 H 中剩下的鄰居構成團。在 G 上照這個順序刪點：歸納地，每一步「G 補邊後的圖」都是「H 剩下的部分」的子圖（補的邊是 v 的剩餘鄰居兩兩之間，而它們在 H 中本來就相鄰）。所以 v 被刪時在 G 補邊圖中的鄰居 ⊆ 它在 H 的剩餘鄰居，後者連同 v 是一個團，大小 ≤ k + 1，鄰居數 ≤ k。∎

這個定理給了兩個算法上的後果。第一，算 treewidth 等於**找最佳消去序**，搜尋空間從「所有樹」縮成「所有排列」。第二，它還可以再縮：消去序的寬度只和「每個點被刪時，哪些點已經被刪」有關，和已刪點的順序無關。

**引理**：設 S 是已經刪掉的點集。v 被刪時（在補邊圖中）的鄰居恰好是 `Q(S, v)`：不在 `S ∪ {v}`、且能從 v 經過一條內部點全在 S 的路徑走到的點。證明對刪點的次數歸納：補邊 a–b 是在刪某個 x 時加的，a、b 都是 x 的鄰居；由歸納假設它們都經由 S 內的路徑連到 x，接起來就是 a 到 b 內部全在 S 的路徑。反向，若 v 到 w 有內部全在 S 的路徑，對路徑上的內部點依刪除順序歸納，每刪一個內部點，它在路徑上的前後兩點被補成相鄰，路徑變短，最後 v、w 直接相鄰。∎

於是有 Bodlaender 等人（2012）的子集 DP：`TW(S)` = 「先刪 S 的所有點（以最佳順序）時，過程中最大的鄰居數」，

```text
TW(∅) = −∞（程式裡用 −1）
TW(S) = min over v ∈ S of max( TW(S − v), |Q(S − v, v)| )
tw(G) = TW(V)
```

時間 `O*(2^n)`，和 Held–Karp 解 TSP 是同一招。下面把它和 n! 暴力、NetworkX 的兩個 heuristic 一起跑，並在 n = 6 的**所有** 32768 張帶標號圖上比較：

```python
# tw_exact.py（需要同目錄的 td_basics.py）
import itertools
from functools import lru_cache
import networkx as nx
from networkx.algorithms.approximation import treewidth_min_degree, treewidth_min_fill_in
from td_basics import td_from_order, width


def order_width(adj, order):
    bags, _, _ = td_from_order(adj, order)
    return width(bags)


def tw_bruteforce(adj):
    """窮舉 n! 個消去序。"""
    return min(order_width(adj, p) for p in itertools.permutations(adj))


def tw_dp(adj):
    """O*(2^n) 的子集 DP（Bodlaender 等人 2012）：
    TW(S) = 先消去 S 裡所有點時，過程中最大的鄰居數；
    TW(S) = min_{v in S} max(TW(S - v), |Q(S - v, v)|)，
    Q(S, v) = 不在 S ∪ {v}、但能從 v 只經過 S 裡的點走到的點。"""
    V = sorted(adj)
    idx = {v: i for i, v in enumerate(V)}
    n = len(V)

    def Q(S, v):
        seen, stack, out = {v}, [v], set()
        while stack:
            u = stack.pop()
            for w in adj[u]:
                if w in seen:
                    continue
                seen.add(w)
                if S >> idx[w] & 1:
                    stack.append(w)       # 走進已消去的點，繼續擴散
                else:
                    out.add(w)            # 碰到還沒消去的點：它是 v 消去時的鄰居
        return len(out)

    @lru_cache(maxsize=None)
    def TW(S):
        if S == 0:
            return -1
        return min(max(TW(S & ~(1 << i)), Q(S & ~(1 << i), V[i]))
                   for i in range(n) if S >> i & 1)

    return TW((1 << n) - 1)


def all_graphs(n):
    pairs = list(itertools.combinations(range(n), 2))
    for mask in range(1 << len(pairs)):
        adj = {v: set() for v in range(n)}
        for b, (u, v) in enumerate(pairs):
            if mask >> b & 1:
                adj[u].add(v); adj[v].add(u)
        yield adj


def nx_graph(adj):
    G = nx.Graph()
    G.add_nodes_from(adj)
    G.add_edges_from((u, v) for u in adj for v in adj[u])
    return G


if __name__ == "__main__":
    named = {
        "path P6": nx.path_graph(6),
        "cycle C6": nx.cycle_graph(6),
        "grid 3x3": nx.grid_2d_graph(3, 3),
        "K5": nx.complete_graph(5),
        "Petersen": nx.petersen_graph(),
    }
    for name, G in named.items():
        adj = {u: set(G[u]) for u in G}
        md, _ = treewidth_min_degree(G)
        mf, _ = treewidth_min_fill_in(G)
        print(f"{name:9s} exact={tw_dp(adj)}  min_degree={md}  min_fill_in={mf}")

    # 在 n=6 的所有 32768 張圖上：DP = 暴力消去序；heuristic 是上界
    worse = {"min_degree": 0, "min_fill_in": 0}
    example = None
    for adj in all_graphs(6):
        ex = tw_dp(adj)
        G = nx_graph(adj)
        md, _ = treewidth_min_degree(G)
        mf, _ = treewidth_min_fill_in(G)
        assert md >= ex and mf >= ex
        worse["min_degree"] += md > ex
        worse["min_fill_in"] += mf > ex
        if md > ex and example is None:
            example = (adj, ex, md)
    print("n=6 全部 32768 張圖：heuristic 比最佳差的次數", worse)
    # 每 16 張抽 1 張（共 2048 張）和 720 個消去序的暴力對拍
    for k, adj in enumerate(all_graphs(6)):
        if k % 16 == 0:
            assert tw_dp(adj) == tw_bruteforce(adj)
    print("DP 與 n! 暴力在 2048 張圖上一致")
    if example:
        adj, ex, md = example
        print("min_degree 失手的例子: 邊", sorted((u, v) for u in adj for v in adj[u] if u < v),
              "exact", ex, "min_degree", md)
```

```text
path P6   exact=1  min_degree=1  min_fill_in=1
cycle C6  exact=2  min_degree=2  min_fill_in=2
grid 3x3  exact=3  min_degree=3  min_fill_in=3
K5        exact=4  min_degree=4  min_fill_in=4
Petersen  exact=4  min_degree=4  min_fill_in=4
n=6 全部 32768 張圖：heuristic 比最佳差的次數 {'min_degree': 15, 'min_fill_in': 0}
DP 與 n! 暴力在 2048 張圖上一致
min_degree 失手的例子: 邊 [(0, 1), (0, 3), (0, 4), (0, 5), (1, 3), (1, 4), (1, 5), (2, 3), (2, 4), (2, 5)] exact 3 min_degree 4
```

這支在本機約 37 秒。失手的例子值得拆開：它是 `K_{3,3}`（左 {0,1,2}、右 {3,4,5}）再加一條邊 0–1。度數：0、1 是 4，其餘是 3。min-degree 在一群度數 3 的點裡挑了 2（左邊那個），刪 2 會把 {3, 4, 5} 補成三角形，剩下的 5 個點變成 K5，寬度 4。最佳做法是先刪右邊的 3：鄰居 {0, 1, 2} 只需補 0–2、1–2 兩條邊，之後寬度一路不超過 3。**同樣是最小度數，選哪一個決定了結果**，heuristic 的 tie-breaking 沒有看 fill-in 的代價；min-fill-in 直接看代價，在 n = 6 上一次都沒失手（但在大圖上它也會失手，下一節會看到）。

## 在樹分解上做 DP：最大獨立集

固定一個根，令 `V_t` 為 t 子樹裡所有袋子的聯集。定義

```text
f_t(S) = G[V_t] 中「和 X_t 的交集恰好是 S」的最大獨立集大小    （S ⊆ X_t 且 S 獨立）
```

**遞迴**：t 的子節點為 c1, …, cr，

```text
f_t(S) = |S| + Σ_c  max{ f_c(S') − |S' ∩ X_t|  :  S' ⊆ X_c 獨立，S' ∩ X_t = S ∩ X_c }
```

答案是 `max_S f_root(S)`。

**正確性**。需要兩個結構引理，都來自規則 (3)：

1. **子樹之間不共用袋子外的點**：若 `u ∈ V_{c1} \ X_t` 且 `u ∈ V_{c2}`（c1 ≠ c2），包含 u 的袋子在 T 上連通，而 c1 子樹到 c2 子樹的樹路徑經過 t，所以 `u ∈ X_t`，矛盾。
2. **子樹外的點和子樹內「袋子外」的點不相鄰**：若 `u ∈ V_c \ X_t`、`w ∉ V_c`，而 uw 是邊，由規則 (2) 某個袋子同時含 u、w。含 u 的袋子都在 c 子樹裡（否則 u 會在 `X_t`），所以 w 也出現在 c 子樹，`w ∈ V_c`，矛盾。

由引理 2，G[V_t] 的獨立集 I（`I ∩ X_t = S`）限制到每個 `V_c` 上還是獨立集，記 `S' = I ∩ X_c`，它必須和 S 在 `X_c ∩ X_t` 上一致。由引理 1，各子樹在 `X_t` 外的部分互不重疊，所以 `|I| = |S| + Σ_c (|I ∩ V_c| − |S' ∩ X_t|)`：子樹貢獻裡和 `X_t` 重疊的點已經算在 |S| 裡，要扣掉。反過來，對每個子節點各自挑一個一致的最佳 `S'` 拼起來，結果是獨立集：子樹內部各自獨立；跨子樹或子樹與 `X_t` 之間若有邊，由引理 2 那條邊的兩端都在某個 `X_c` 裡，已經被 `S'` 的獨立性排除。所以遞迴取到的最大值就是 `f_t(S)`。∎

每個袋子的獨立子集至多 `2^(w+1)` 個，合併一條樹邊是 `O(4^(w+1))`（兩邊的表格兩兩比對；用 nice tree decomposition 可以降到 `O(2^w)` 級），總時間 `O(4^w · n)` 級。

```python
# td_mis.py（需要同目錄的 td_basics.py）
import itertools
import random
import time
import networkx as nx
from networkx.algorithms.approximation import treewidth_min_fill_in
from td_basics import is_tree_decomposition


def mis_on_td(adj, bags, tree):
    """在樹分解上做 DP，回傳最大獨立集（一個集合）。
    f[t][S] = G[V_t] 裡「和袋子 X_t 的交集恰好是 S」的最大獨立集，V_t = t 子樹所有袋子的聯集。"""
    root = next(iter(tree))
    parent, order, stack = {root: None}, [], [root]
    while stack:                                   # 先序走訪，倒過來就是後序
        t = stack.pop()
        order.append(t)
        for c in tree[t]:
            if c not in parent:
                parent[c] = t
                stack.append(c)
    children = {t: [c for c in tree[t] if parent.get(c) == t] for t in tree}

    def independent_subsets(B):
        B = sorted(B, key=repr)
        for r in range(len(B) + 1):
            for S in itertools.combinations(B, r):
                if all(b not in adj[a] for a, b in itertools.combinations(S, 2)):
                    yield frozenset(S)

    f = {}
    for t in reversed(order):
        X = bags[t]
        table = {}
        for S in independent_subsets(X):
            best = set(S)
            for c in children[t]:
                # 子節點的選法 S' 必須和 S 在共用的點上一致
                cand = [(len(I) - len(Sc & X), I) for Sc, I in f[c].items()
                        if Sc & X == S & bags[c]]
                gain, I = max(cand, key=lambda p: p[0])
                best |= I
            table[S] = best
        f[t] = table
    return max(f[root].values(), key=len)


def brute_mis(adj):
    V = list(adj)
    for r in range(len(V), -1, -1):
        for S in itertools.combinations(V, r):
            if all(b not in adj[a] for a, b in itertools.combinations(S, 2)):
                return set(S)


def nx_td(G):
    w, T = treewidth_min_fill_in(G)
    bags = {B: B for B in T.nodes}           # NetworkX 的樹分解：節點本身就是 frozenset 袋子
    tree = {B: set(T[B]) for B in T.nodes}
    return w, bags, tree


if __name__ == "__main__":
    rng = random.Random(36)
    for trial in range(300):
        n = rng.randint(1, 14)
        G = nx.gnp_random_graph(n, rng.choice([0.15, 0.3, 0.5]), seed=rng.randrange(10**6))
        adj = {u: set(G[u]) for u in G}
        w, bags, tree = nx_td(G)
        assert is_tree_decomposition(adj, bags, tree)[0]
        I = mis_on_td(adj, bags, tree)
        assert all(b not in adj[a] for a, b in itertools.combinations(I, 2))
        assert len(I) == len(brute_mis(adj)), (n, sorted(G.edges()))
    print("300 張隨機圖 (n <= 14)：樹分解 DP = 暴力")

    # 寬度小、點很多：暴力完全不可能，DP 照樣跑
    for rows, cols in ((3, 100), (4, 100), (5, 100), (6, 100)):
        G = nx.convert_node_labels_to_integers(nx.grid_2d_graph(rows, cols))
        adj = {u: set(G[u]) for u in G}
        w, bags, tree = nx_td(G)
        t0 = time.perf_counter()
        I = mis_on_td(adj, bags, tree)
        dt = time.perf_counter() - t0
        # 格子圖是二分圖且有完美/近完美匹配，最大獨立集 = ceil(rows*cols/2)
        print(f"grid {rows}x{cols}: n={rows*cols} width={w} |MIS|={len(I)} "
              f"(預期 {-(-rows*cols // 2)}) DP {dt:.2f}s")
```

```text
300 張隨機圖 (n <= 14)：樹分解 DP = 暴力
grid 3x100: n=300 width=3 |MIS|=150 (預期 150) DP 0.02s
grid 4x100: n=400 width=4 |MIS|=200 (預期 200) DP 0.06s
grid 5x100: n=500 width=5 |MIS|=250 (預期 250) DP 0.42s
grid 6x100: n=600 width=7 |MIS|=300 (預期 300) DP 1.30s
```

讀這張表的三個重點：

1. **對拍**：300 張隨機圖上，DP 的答案和 `2^n` 暴力一致，而且我們先用自己的檢查器確認 NetworkX 給的樹分解合法，再拿它做 DP。格子圖的預期值來自 [Ch 22](./22-bipartite-matching.md)：格子圖是二分圖，`α = n − ν`（König），`k × 100` 格子有完美匹配，`α = n/2`。
2. **時間隨寬度指數成長，隨 n 線性**：600 點的格子圖，暴力要看 `2^600` 個子集；DP 在 1.3 秒內完成。寬度 3 → 4 → 5 時時間約乘 3 倍、7 倍，5 → 7 只乘了 3 倍；袋子數、每個袋子裡實際的獨立子集數都在變，只能說量級上和「指數於 w」一致，不能從這四個點擬合出底數。
3. **heuristic 給了 7，但 6×100 格子的 treewidth 是 6**：min-fill-in 在這張圖上多了 1，DP 的表格就大了 4 倍左右。在 treewidth DP 裡，分解的品質直接乘在指數上。

## 對比與取捨

| 方法 | 給出什麼 | 時間 | 適用 |
| --- | --- | --- | --- |
| 子集 DP（本章 `tw_dp`） | 精確 treewidth | `O*(2^n)` | n ≤ 25 左右 |
| min-degree / min-fill-in（NetworkX） | 上界與分解 | 多項式 | 任何大小；先用它 |
| Bodlaender 線性時間演算法（1996） | 寬度 ≤ k 的分解或回報不存在 | `2^(O(k³)) · n` | 理論上 FPT；常數大到實務不用 |
| PACE 競賽的 solver | 精確或高品質上界 | 實務快 | 幾千點的真實圖 |
| 一般的分支搜尋（Ch 34） | 單一問題的解 | 指數於 n | 沒有小分隔集時 |

**為什麼不直接用 SAT（[Ch 35](./35-reductions-to-sat.md)）？** 可以，而且對中等大小的一般圖常常更快。但 treewidth DP 給的是**保證**：寬度 w 的圖，時間上界是 `c^w · n`，和實例無關；SAT 沒有這種保證。反過來，寬度大的圖（隨機圖的 treewidth 是 `Θ(n)` 級），treewidth DP 毫無用處。

## Courcelle 定理（陳述）

**定理（Courcelle 1990）**：對任何可以用**單元二階邏輯**（monadic second-order logic, MSO₂）寫出的圖性質 φ，以及任何常數 k，存在演算法在 treewidth ≤ k 的圖上以 `f(k, |φ|) · n` 時間判定 φ。

MSO₂ 允許對點、邊、點集、邊集做量化，加上「u 和 e 相接」這種基本關係。例子：

```text
3-可著色：  ∃ X1 ∃ X2 ∃ X3 [ 每個點恰在一個 Xi 裡 ∧ ∀ 邊 uv：u, v 不在同一個 Xi ]
獨立集 ≥ k（k 固定）：可寫成 k 個點變數的一階式子
Hamilton cycle：∃ 邊集 F [ 每點在 F 中度數 2 ∧ F 連通 ]（連通性可用集合量化表達）
```

所以 3-著色、Hamilton cycle 等等在 treewidth 有界的圖上都是**線性時間**。這裡只給陳述，不給證明。證明的架構是：把有界 treewidth 圖看成某種有限代數的項（term），MSO 式子對應到樹自動機（tree automaton），在分解樹上跑自動機。

**它藏了什麼**：`f(k, |φ|)` 一般而言是 k 與式子量詞交替深度的**塔狀指數**（`2^2^…^k`），Frick 與 Grohe 證明了在合理的複雜度假設下這是無法避免的。所以 Courcelle 定理是「分類工具」：它告訴你某個問題在有界 treewidth 上是 FPT，然後你應該像本章做 MIS 那樣，**手寫一個專用 DP**。

## 踩雷集錦

1. **把 heuristic 的寬度當成 treewidth**：`treewidth_min_fill_in` 回傳的是**上界**。6×100 格子實測回報 7，真值是 6。寫論文或下結論時，必須說「我們找到寬度 7 的分解」，而不是「treewidth 是 7」。
2. **忘了規則 (3)**：只檢查「點有被蓋到、邊有被蓋到」的樹分解是錯的。少了連通性，DP 的正確性引理 1、2 都不成立，子樹之間會重複計算同一點。寫 DP 之前先跑 `is_tree_decomposition`，本章每個對拍都這樣做。
3. **以為 treewidth 小代表圖小或稀疏**：K_{2,n} 的 treewidth 是 2，有 2n 條邊；`k × n` 格子的 treewidth 是 k，n 可以任意大。反過來，3-正則的 expander（[Ch 30](./30-expander-graphs.md)）每點只有 3 條邊，treewidth 卻是 `Θ(n)`。稀疏不等於好分解。
4. **子樹貢獻重複計數**：只存大小（`f_t(S)` 是整數）的實作，合併時忘了減 `|S' ∩ X_t|`，共享點會被每個子節點各算一次，答案偏大。本章的 `td_mis.py` 存的是集合，`best |= I` 的聯集自動去重，而且在同一個 S 下所有候選的 `|S' ∩ X_t|` 都等於 `|S ∩ X_c|`，扣不扣不影響挑哪個；所以這個 bug 在集合版不會出現，改寫成只存大小的版本時才會冒出來。我寫了只存大小的版本實測：有扣時 300 張全對；拿掉減法後 300 張裡有 191 張答案偏大。
5. **把 Courcelle 的「線性時間」當成實用演算法**：常數是塔狀指數，沒有人照著 Courcelle 的證明寫程式。它的用途是證明可解性，然後你手寫 DP。

## 進階：再往深一層

- **Nice tree decomposition**：把分解整理成只有四種節點（leaf、introduce v、forget v、join），每種節點的轉移只處理一個點，DP 寫起來更規律，join 可以做到 `O(2^w)` 而不是 `O(4^w)`。Cygan 等人的教科書第 7 章用這個形式寫所有 DP。
- **下界**：在 SETH（Strong ETH）下，最大獨立集在寬度 w 的分解上沒有 `(2 − ε)^w · poly(n)` 演算法（Lokshtanov、Marx、Saurabh 2011）。所以 `2^w` 級的 DP 基本上是最佳的。
- **Pathwidth 與 tree-depth**：把 T 限制成路徑得到 pathwidth（`pw ≥ tw`）；限制成高度有界的樹得到 tree-depth。不同的寬度參數對應不同的 DP 空間/時間取捨。
- **編譯器裡的 treewidth**：Thorup 的 `tw ≤ 6` 讓結構化程式的 CFG 可以用樹分解做最佳暫存器分配等問題；後續有人實測 Java 程式的 CFG，平均 treewidth 不到 3。這和 [Ch 17](./17-register-allocation.md) 的「SSA 讓 interference graph 變 chordal」是同一個主題的兩面：結構讓 NP-hard 變可解。

## 動手練習

1. 把 `td_mis.py` 改成算最大**加權**獨立集（每點一個權重），用 `nx.max_weight_clique(nx.complement(G), weight="weight")` 對拍。
2. 用 `tw_dp` 驗證 `k × n` 格子（k ≤ 3、n ≤ 7）的 treewidth 是 `min(k, n)`，並找出 `treewidth_min_degree` 第一個失手的格子大小（如果有）。
3. 在樹分解上寫 3-著色的判定 DP（每個袋子記錄袋子內點的合法著色集合），在 Ch 34 的 `sat_to_3col` 產生的圖上跑，和 `three_color` 對拍。觀察那些圖的寬度是多少。

## 本章重點整理

- 樹分解：袋子沿著樹排列，蓋住所有點與邊，同一點的袋子連通；寬度 = 最大袋子 − 1。
- treewidth = 最佳消去序的最大鄰居數 = 最小 chordal 補圖的 ω − 1；子集 DP 在 `O*(2^n)` 精確算出，heuristic 只給上界。
- 在寬度 w 的分解上，最大獨立集是 `O(c^w · n)`：子問題只透過袋子溝通，正確性來自規則 (3) 推出的兩個分隔引理。
- Courcelle：MSO₂ 可表達的性質在有界 treewidth 上都是線性時間，但常數是塔狀指數；它是分類工具，不是實作藍圖。

## 自我檢核

- [ ] 不看筆記，能寫出樹分解的三條規則，並舉一個「只違反第 3 條」的例子
- [ ] 能說明為什麼消去序的寬度只和「已刪點集合」有關，因此 DP 可以在 `2^n` 個子集上做
- [ ] 能用自己的話證明 MIS DP 合併時為什麼要扣掉 `|S' ∩ X_t|`
- [ ] 有人說「這張圖只有 3n 條邊，treewidth 一定很小」，你能舉出反例嗎？
- [ ] 想一個你處理過的圖（CFG、依賴圖、網路拓撲），猜它的 treewidth 大概是多少，並用 `treewidth_min_fill_in` 驗證

## 延伸閱讀

### 論文

- **[Graph Minors. II. Algorithmic Aspects of Tree-Width](https://doi.org/10.1016/0196-6774(86)90023-4)** — Neil Robertson、Paul D. Seymour，Journal of Algorithms 7(3)，pp. 309–322（1986）
  - **核心貢獻**：引入 tree-width，並用它給出「判斷圖是否含某個固定平面圖為子式」的多項式演算法。
  - **讀哪裡**：前兩節的定義與基本性質；後半依賴 Graph Minors 系列的其他篇，可以跳過。
  - **和本章的關聯**：本章的定義出處。
- **[On Exact Algorithms for Treewidth](https://doi.org/10.1145/2390176.2390188)** — Hans L. Bodlaender、Fedor V. Fomin、Arie M. C. A. Koster、Dieter Kratsch、Dimitrios M. Thilikos，ACM Transactions on Algorithms 9(1)，Article 12（2012；初版 ESA 2006）
  - **核心貢獻**：`O*(2^n)` 的子集 DP 與多項式空間的 `O*(2.9512^n)` 演算法，並有實驗。
  - **讀哪裡**：DP 那一節的 `TW(S)` 遞迴與 `Q(S, v)` 的引理，就是本章 `tw_dp` 的來源。
  - **前提**：本章的消去序節。
- **[Treewidth Computations I. Upper Bounds](https://ics-archive.science.uu.nl/research/techreps/repo/CS-2008/2008-032.pdf)** — Hans L. Bodlaender、Arie M. C. A. Koster，Information and Computation 208(3)，pp. 259–275（2010）
  - **核心貢獻**：整理 treewidth 上界 heuristic（greedy degree、greedy fill-in 等）與近似演算法，附實驗比較。
  - **讀哪裡**：greedy 消去序那一節，了解 NetworkX 兩個函式背後的家族，以及 tie-breaking 的影響。連結是 Utrecht 的技術報告版。
- **[The Monadic Second-Order Logic of Graphs I. Recognizable Sets of Finite Graphs](https://www.labri.fr/perso/courcell/Textes1/MSOL01(1990).pdf)** — Bruno Courcelle，Information and Computation 85(1)，pp. 12–75（1990）
  - **核心貢獻**：Courcelle 定理系列的第一篇，證明 MSO 可定義的圖集合是 recognizable。
  - **讀哪裡**：很長、很代數；建議只讀引言知道結果的形狀，演算法版的陳述讀下面的教科書。
- **All Structured Programs Have Small Tree Width and Good Register Allocation** — Mikkel Thorup，Information and Computation（1998），pp. 159–181
  - **核心貢獻**：不含 goto 的結構化程式，CFG 的 treewidth 至多 6（隨語言構造而定的小常數），並據此給暫存器分配的近似保證。
  - **讀哪裡**：前段的 treewidth 上界證明（對程式結構歸納）；連到 [Ch 17](./17-register-allocation.md) 與 [compilers/compiler_backend](../../compilers/compiler_backend/README.md)。

### 書籍

- **《Parameterized Algorithms》** — Marek Cygan、Fedor V. Fomin、Łukasz Kowalik、Daniel Lokshtanov、Dániel Marx、Marcin Pilipczuk、Michał Pilipczuk、Saket Saurabh（Springer，2015）
  - **這本書的定位**：參數化演算法的標準教科書，本章之後最該讀的一本。
  - **讀哪幾章**：第 7 章 Treewidth：nice tree decomposition、各種 DP（獨立集、著色、Steiner tree）、Courcelle 定理的演算法版陳述；第 14 章的下界可以之後再看。

Treewidth 讓我們利用圖的結構把 NP-hard 問題變簡單。下一章看一個地位更奇特的問題：圖同構既沒被證明是 NP-complete，也沒被證明在 P 裡；而它的實務演算法，核心是一個二十行就能寫完的著色細分程序。

→ [Ch 37 圖同構與 Weisfeiler–Leman](./37-graph-isomorphism-wl.md)
