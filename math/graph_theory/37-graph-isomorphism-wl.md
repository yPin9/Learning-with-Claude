# Ch 37 — 圖同構與 Weisfeiler–Leman

> **目標**：知道圖同構問題在複雜度地圖上的奇特位置；親手實作 1-WL（color refinement），證明它的正確性與收斂性，並找出它分不開的圖（正則圖、strongly regular graph、7 點以下 atlas 裡的 26 對）；用 individualization-refinement 寫出一個完整的同構判定器並和 NetworkX 對拍；能說清楚 CFI 構造在證明什麼、Babai 的準多項式時間結果是什麼。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11（AMD Ryzen 7 5700G）。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。跑範例前設 `PYTHONUTF8=1`。

## 為什麼需要這個？

[Ch 7](./07-graph-definitions.md) 定義了同構：兩張圖之間存在一個保持相鄰關係的點雙射。檢查一個給定的雙射是 `O(n + m)`，所以圖同構（Graph Isomorphism, GI）在 NP 裡。可是過去五十年，沒有人證明它是 NP-complete，也沒有人找到多項式演算法。它是 [Ch 34](./34-np-complete-reductions.md) 提過的 Ladner 定理最著名的「自然候選人」：如果 P ≠ NP，NP-intermediate 問題一定存在，而 GI 是少數讓人真心懷疑落在中間的問題之一。

證據有兩面：

- **它大概不是 NP-complete**：GI 的補問題（判定**不**同構）有一個交互式證明（Goldreich–Micali–Wigderson 的經典協定），GI 在 coAM 裡。如果 GI 是 NP-complete，多項式階層會塌到第二層（Boppana–Håstad–Zachos 1987）。複雜度學界普遍認為這不會發生。
- **它在實務上幾乎總是很快**：McKay 的 nauty 從 1981 年開始處理幾十萬點的圖，絕大多數實例瞬間解決。難的實例要刻意構造。

2015 年 12 月，Babai 宣布 GI 可以在**準多項式時間**（quasipolynomial time）`exp((log n)^O(1))` 內解決，這是三十多年來最大的突破。本章的主角 Weisfeiler–Leman 演算法，就是 Babai 演算法、nauty 與幾乎所有實務 GI 工具裡的第一道工序。

對我們的應用：[Ch 40](./40-binary-similarity.md) 會把 binary 的函式 CFG 用 WL 著色變成特徵向量，做跨編譯器、跨最佳化等級的函式相似度比對。這章是它的數學底座。

## 先建立直覺

把 color refinement 想成一群人玩「自我介紹」遊戲：

```text
第 0 輪：每個人只說「我是人」（全部同色）
第 1 輪：每個人說「我是人，我有 3 個朋友」          → 依度數分色
第 2 輪：「我是 A 色，我的朋友是 2 個 A 色 1 個 B 色」 → 依「鄰居顏色的多重集」再分
...
直到沒有人能被進一步區分
```

兩張圖如果同構，同構映射會把「同一種自我介紹」的人對應起來，所以每一輪的**顏色直方圖**（每種顏色幾個人）一定相同。反過來，只要某一輪直方圖不同，兩張圖就一定不同構。這是單向的測試：直方圖一路相同，**不代表**同構。

```text
P4 ：  o───o───o───o          第 1 輪：度數 {1,2,2,1}   → 直方圖 {deg1: 2, deg2: 2}
K1,3：     o                  第 1 輪：度數 {3,1,1,1}   → 直方圖 {deg3: 1, deg1: 3}
          /|\                 不同 → 第 1 輪就分開
         o o o

C6 ：   o─o─o                 每個點度數 2，鄰居都是度數 2 ……
        │   │                 每一輪大家的自我介紹都一樣
        o─o─o                 → 1-WL 永遠分不開
2×C3：  o─o   o─o
         \│    \│
          o     o
```

## 1-WL 的實作

**演算法（color refinement）**：初始著色 `c_0(v) = 0`（有點標籤就用標籤）。每一輪

```text
c_{i+1}(v) = 雜湊( c_i(v), {{ c_i(w) : w ∈ N(v) }} )      {{ }} 是多重集
```

當某一輪的顏色類數不再增加就停。要比較兩張圖，**兩張圖必須共用同一本顏色字典**，否則「顏色 3」在兩張圖裡指的是不同的東西。

```python
# wl.py
from collections import Counter
import networkx as nx


def color_refinement(graphs, max_iter=None):
    """1-WL 同時跑在多張圖上（共用同一本「顏色字典」，顏色才可以跨圖比較）。
    graphs: list[adj]。回傳每輪每張圖的顏色直方圖，以及最後的著色。"""
    colors = [{v: 0 for v in adj} for adj in graphs]
    history = []
    rounds = 0
    while True:
        palette = {}
        new = []
        for adj, col in zip(graphs, colors):
            nc = {}
            for v in adj:
                sig = (col[v], tuple(sorted(Counter(col[w] for w in adj[v]).items())))
                if sig not in palette:
                    palette[sig] = len(palette)     # 依第一次出現的順序編號
                nc[v] = palette[sig]
            new.append(nc)
        rounds += 1
        hist = [Counter(nc.values()) for nc in new]
        history.append(hist)
        stable = all(len(set(nc.values())) == len(set(oc.values()))
                     for nc, oc in zip(new, colors))
        colors = new
        if stable or (max_iter and rounds >= max_iter):
            return history, colors


def wl_distinguishes(adj1, adj2):
    history, _ = color_refinement([adj1, adj2])
    for r, (h1, h2) in enumerate(history, 1):
        if h1 != h2:
            return r
    return None


def to_adj(G):
    return {u: set(G[u]) for u in G}


if __name__ == "__main__":
    # 例 1：路徑 P4 和星形 K_{1,3}：點數邊數都一樣
    P4, S3 = to_adj(nx.path_graph(4)), to_adj(nx.star_graph(3))
    print("P4 vs K1,3：第", wl_distinguishes(P4, S3), "輪分開")
    hist, cols = color_refinement([P4])
    print("P4 每輪顏色直方圖", [dict(h[0]) for h in hist])

    # 例 2：C6 vs 兩個 C3：都是 2-正則，1-WL 分不開
    C6 = to_adj(nx.cycle_graph(6))
    C3x2 = to_adj(nx.disjoint_union(nx.cycle_graph(3), nx.cycle_graph(3)))
    print("C6 vs 2*C3：", wl_distinguishes(C6, C3x2))

    # 例 3：任兩張點數相同的 d-正則圖都分不開
    A = to_adj(nx.random_regular_graph(3, 10, seed=1))
    B = to_adj(nx.random_regular_graph(3, 10, seed=2))
    print("兩張 3-正則 10 點圖：同構?", nx.is_isomorphic(nx.Graph(A), nx.Graph(B)),
          "1-WL:", wl_distinguishes(A, B))

    # 和 NetworkX 的 WL hash 對答案
    for G1, G2, name in ((nx.path_graph(4), nx.star_graph(3), "P4/K1,3"),
                         (nx.cycle_graph(6), nx.disjoint_union(nx.cycle_graph(3), nx.cycle_graph(3)), "C6/2C3")):
        h1, h2 = nx.weisfeiler_lehman_graph_hash(G1), nx.weisfeiler_lehman_graph_hash(G2)
        print(f"nx WL hash {name}: {'相同' if h1 == h2 else '不同'}")
```

```text
P4 vs K1,3：第 1 輪分開
P4 每輪顏色直方圖 [{0: 2, 1: 2}, {0: 2, 1: 2}]
C6 vs 2*C3： None
兩張 3-正則 10 點圖：同構? False 1-WL: None
nx WL hash P4/K1,3: 不同
nx WL hash C6/2C3: 相同
```

stderr 上另外會出現一行 NetworkX 的 `UserWarning`：沒有點/邊屬性的圖，WL hash 的值在 v3.5 因為 bug 修正而改變了。這對 Ch 40 很重要，見踩雷 4。

P4 的直方圖第 2 輪和第 1 輪一樣（兩個端點、兩個中間點），類數沒增加，演算法停在第 2 輪。兩張 3-正則圖不同構，但 1-WL 給出 None：這不是 bug，是 1-WL 的本質限制，下面證明。

## 底層機制：正確性、收斂與它看得見什麼

**命題 1（同構不變）**：若 `φ: G → H` 是同構，則對每一輪 i 與每個點 v，`c_i(v) = c_i(φ(v))`（共用字典時）。因此每一輪直方圖相同。

證明對 i 歸納。i = 0 時全部是 0。設第 i 輪成立。φ 把 v 的鄰居一對一映到 `φ(v)` 的鄰居，所以 `{{c_i(w) : w ∈ N(v)}} = {{c_i(φ(w)) : w ∈ N(v)}} = {{c_i(u) : u ∈ N(φ(v))}}`，再加上 `c_i(v) = c_i(φ(v))`，兩者的 signature 相同，共用字典給同一個新顏色。∎

**命題 2（收斂）**：每一輪的分類是上一輪的細分（signature 裡含舊顏色，所以舊顏色不同的點新顏色也不同）。若某一輪類數沒有增加，分類就等於上一輪，之後也不再變化。類數至多 n，所以至多 n 輪就穩定。

**命題 3（正則圖分不開）**：任兩張 n 點 d-正則圖，1-WL 的直方圖每一輪都相同。證明：第 1 輪每個點的 signature 都是 `(0, {{0 × d}})`，全部同色；第 2 輪 signature 全部是 `(c, {{c × d}})`，還是全部同色，以此類推。∎

穩定著色有一個乾淨的組合刻畫：它是**最粗的 equitable partition**，也就是每個色類 P 裡的每個點，在每個色類 Q 裡的鄰居數都相同。正則圖的「全部一色」本身就是 equitable，所以 1-WL 一步都動不了。這也是為什麼 1-WL 和 [Ch 27](./27-spectral-graph-theory.md) 的譜方法常一起出現：equitable partition 的商矩陣（quotient matrix）的特徵值都是原鄰接矩陣的特徵值。

複雜度：照本章寫法每輪 `O((n + m) log n)`（排序多重集），最多 n 輪，`O(n (n + m) log n)`。用 Hopcroft 式的「只處理較小那一半」分割細分技巧，可以做到 `O((n + m) log n)` 總時間（Cardon–Crochemore、Paige–Tarjan 那一系的結果；我沒有實作這個版本）。

### 1-WL 在小圖上的實際辨識力

命題 3 讓人以為 1-WL 只在正則圖上失敗。用 NetworkX 的 graph atlas（所有 7 點以下的圖，1253 張，兩兩不同構）實測：

```text
atlas：點數邊數相同的不同構圖對 55770 對，1-WL 分不開 26 對
前幾對 (n, m, 度數序列): [((6, 6), [2, 2, 2, 2, 2, 2]), ((6, 7), [2, 2, 2, 2, 3, 3]),
                          ((6, 8), [2, 2, 3, 3, 3, 3]), ((6, 9), [3, 3, 3, 3, 3, 3]),
                          ((7, 6), [0, 2, 2, 2, 2, 2, 2]), ((7, 7), [0, 2, 2, 2, 2, 3, 3])]
```

（這是下一節 `wl_ir.py` 的輸出片段，為了閱讀把清單折行。）26 對裡只有 4 對是正則圖，另外 22 對不是。第二對是 `(6, 7)`：一個是 6-cycle 加一條弦、一個是兩個三角形用一條邊接起來（「啞鈴」），度數序列都是 `[2,2,2,2,3,3]`，而且兩個度數 3 的點相鄰、各自的鄰居色分布也一樣。1-WL 本質上只看「從每個點出發的樹狀展開」（universal cover），**看不見環的長度**。這是它最該記住的弱點。

反過來，Babai、Erdős、Selkow（1980）證明了：均勻隨機的 n 點圖，以趨近 1 的機率在 1-WL 之後每個點顏色都不同（discrete coloring），於是同構可以直接讀出來。這解釋了「為什麼實務上 GI 很快」：隨機的、不對稱的圖，1-WL 一步到位。

## Individualization-refinement：把 1-WL 變成完整的判定器

1-WL 卡住時（還有多個點同色），**挑一個色類，在 G 裡挑一點 v，在 H 裡同色類的每一點 w 逐一嘗試**：給 v 和 w 一個全新的顏色（individualize），再 refine。若最後著色是離散的（每色一點），顏色決定唯一的雙射，直接檢查它是不是同構。這就是 nauty、Traces、bliss 的骨架；真實工具還加了自同構剪枝、不變量、canonical labeling，這裡不做。

正確性：若 G ≅ H，取一個同構 φ，`φ(v)` 一定和 v 同色（命題 1，individualize 之後也成立，因為我們給 v、`φ(v)` 相同的新顏色，相當於在兩張圖上加了一致的標籤），所以分支裡一定有一支嘗試 `w = φ(v)`，遞迴下去會找到同構。若不同構，最後檢查雙射那一步永遠不會成功。

下面用它處理一對經典的硬例子：**4×4 rook's graph**（`K4 □ K4`，同列或同行的格子相鄰）與 **Shrikhande graph**（`Z4 × Z4` 上連 `±(1,0), ±(0,1), ±(1,1)`）。兩者都是參數 `(16, 6, 2, 2)` 的 strongly regular graph：16 點、6-正則、相鄰兩點恰有 2 個共同鄰居、不相鄰兩點也恰有 2 個。

```python
# wl_ir.py（需要同目錄的 wl.py）
import itertools
import random
from collections import Counter
import networkx as nx
from wl import wl_distinguishes, to_adj


def joint_refine(a1, c1, a2, c2):
    """兩張圖用同一本字典一起 refine；回傳 (c1, c2) 或 None（直方圖已經不同）。"""
    while True:
        s1 = {v: (c1[v], tuple(sorted(Counter(c1[w] for w in a1[v]).items()))) for v in a1}
        s2 = {v: (c2[v], tuple(sorted(Counter(c2[w] for w in a2[v]).items()))) for v in a2}
        if Counter(s1.values()) != Counter(s2.values()):
            return None
        rank = {s: i for i, s in enumerate(sorted(set(s1.values())))}
        n1 = {v: rank[s1[v]] for v in a1}
        n2 = {v: rank[s2[v]] for v in a2}
        if len(set(n1.values())) == len(set(c1.values())):
            return n1, n2
        c1, c2 = n1, n2


def isomorphic_ir(a1, a2, c1=None, c2=None):
    """Individualization-refinement：refine；若還有同色類，在 G1 挑一點、
    和 G2 同色類的每一點逐一配對（給它們一個新顏色），遞迴。nauty/Traces 的骨架。"""
    if len(a1) != len(a2):
        return False
    c1 = c1 or {v: 0 for v in a1}
    c2 = c2 or {v: 0 for v in a2}
    res = joint_refine(a1, c1, a2, c2)
    if res is None:
        return False
    c1, c2 = res
    classes = Counter(c1.values())
    if all(k == 1 for k in classes.values()):
        # 離散著色：顏色決定唯一的雙射，直接檢查它是不是同構
        inv2 = {c: v for v, c in c2.items()}
        f = {v: inv2[c1[v]] for v in a1}
        return all({f[w] for w in a1[v]} == a2[f[v]] for v in a1)
    target = min(c for c, k in classes.items() if k > 1)
    v1 = min(v for v in a1 if c1[v] == target)
    fresh = max(c1.values()) + 1
    for v2 in [v for v in a2 if c2[v] == target]:
        d1, d2 = dict(c1), dict(c2)
        d1[v1], d2[v2] = fresh, fresh
        if isomorphic_ir(a1, a2, d1, d2):
            return True
    return False


def shrikhande():
    S = [(1, 0), (3, 0), (0, 1), (0, 3), (1, 1), (3, 3)]
    G = nx.Graph()
    for a, b in itertools.product(range(4), repeat=2):
        for da, db in S:
            G.add_edge((a, b), ((a + da) % 4, (b + db) % 4))
    return nx.convert_node_labels_to_integers(G)


if __name__ == "__main__":
    rook = nx.convert_node_labels_to_integers(
        nx.cartesian_product(nx.complete_graph(4), nx.complete_graph(4)))
    shr = shrikhande()
    for name, G in (("4x4 rook", rook), ("Shrikhande", shr)):
        degs = {d for _, d in G.degree()}
        tri = sum(nx.triangles(G).values()) // 3
        print(f"{name:10s} n={G.number_of_nodes()} m={G.number_of_edges()} 度數={degs} 三角形={tri}")
    A, B = to_adj(rook), to_adj(shr)
    print("1-WL 分得開?", wl_distinguishes(A, B))
    print("鄰域導出子圖：rook", sorted(d for _, d in rook.subgraph(rook[0]).degree()),
          "連通分量", nx.number_connected_components(rook.subgraph(rook[0])),
          "| Shrikhande", nx.number_connected_components(shr.subgraph(shr[0])))
    print("individualization-refinement 判定同構?", isomorphic_ir(A, B),
          "| nx.is_isomorphic:", nx.is_isomorphic(rook, shr))

    # 隨機對拍：同構的打亂副本 + 不同構但度數序列相同的圖
    rng = random.Random(37)
    agree = 0
    for trial in range(300):
        n = rng.randint(1, 12)
        G = nx.gnp_random_graph(n, rng.random(), seed=rng.randrange(10**6))
        if rng.random() < 0.5:
            perm = list(G)
            rng.shuffle(perm)
            H = nx.relabel_nodes(G, dict(zip(G, perm)))
        else:
            H = G.copy()
            if H.number_of_edges() >= 2:
                try:
                    nx.double_edge_swap(H, nswap=1, max_tries=100, seed=rng.randrange(10**6))
                except nx.NetworkXException:
                    pass
        assert isomorphic_ir(to_adj(G), to_adj(H)) == nx.is_isomorphic(G, H)
        agree += 1
    print(f"{agree} 組隨機圖對：individualization-refinement 與 nx.is_isomorphic 一致")

    # graph atlas：所有 7 點以下的圖（1253 張，兩兩不同構），1-WL 分不開幾對？
    atlas = nx.graph_atlas_g()
    by_key = {}
    for G in atlas:
        by_key.setdefault((G.number_of_nodes(), G.number_of_edges()), []).append(G)
    fail = []
    total = 0
    for key, gs in by_key.items():
        for G, H in itertools.combinations(gs, 2):
            total += 1
            if wl_distinguishes(to_adj(G), to_adj(H)) is None:
                fail.append((key, sorted(d for _, d in G.degree())))
    print(f"atlas：點數邊數相同的不同構圖對 {total} 對，1-WL 分不開 {len(fail)} 對")
    print("前幾對 (n, m, 度數序列):", fail[:6])
```

```text
4x4 rook   n=16 m=48 度數={6} 三角形=32
Shrikhande n=16 m=48 度數={6} 三角形=32
1-WL 分得開? None
鄰域導出子圖：rook [2, 2, 2, 2, 2, 2] 連通分量 2 | Shrikhande 1
individualization-refinement 判定同構? False | nx.is_isomorphic: False
300 組隨機圖對：individualization-refinement 與 nx.is_isomorphic 一致
atlas：點數邊數相同的不同構圖對 55770 對，1-WL 分不開 26 對
前幾對 (n, m, 度數序列): [((6, 6), [2, 2, 2, 2, 2, 2]), ((6, 7), [2, 2, 2, 2, 3, 3]), ((6, 8), [2, 2, 3, 3, 3, 3]), ((6, 9), [3, 3, 3, 3, 3, 3]), ((7, 6), [0, 2, 2, 2, 2, 2, 2]), ((7, 7), [0, 2, 2, 2, 2, 3, 3])]
```

整支在本機約 7 秒。（atlas 那行和上一節貼的是同一次輸出，我只在上一節把清單折行了。）300 組隨機圖對裡有 255 組同構（打亂標號）、45 組不同構（一次 double edge swap：保持度數序列，卻通常改變結構），全部和 `nx.is_isomorphic` 一致。

rook 與 Shrikhande 的拆解：點數、邊數、度數、三角形數（32）全部相同，1-WL 分不開（命題 3）。但每個點的**鄰域導出子圖**不同：rook 的鄰域是兩個不相交的三角形（同列 3 點、同行 3 點），Shrikhande 是一個 6-cycle。individualize 一個點 v，它的鄰居就和非鄰居分開，再 refine 一次，鄰居之間的結構差異（兩個三角形 vs. 一個圈）立刻反映在顏色上，所以 IR 一層就判定不同構。

## k-WL、CFI 構造與 Babai

**k-WL**：不是給點上色，而是給**有序 k 元組**上色。以 Weisfeiler 與 Leman 1968 年原始的二維版本為例：給點對 `(u, v)` 上色，初始色是「u = v？相鄰？」，每輪對每個第三點 w 收集顏色對 `(c(u, w), c(w, v))` 的多重集來細分。它能看見「u、v 有幾個共同鄰居」這種 1-WL 看不見的資訊。可是 rook 與 Shrikhande 是參數相同的 strongly regular graph，任兩點的共同鄰居數只取決於它們相不相鄰，這個二維版本在它們上面也無法區分（這是 strongly regular graph 的標準事實；我沒有實作它來驗證，留作練習 2）。要分開它們，得用更高維的 WL，或像上一節那樣做 individualization。

要注意文獻裡「k-WL」的編號有兩套慣例（folklore 版與 oblivious 版差一維），讀論文時先確認作者用的是哪一套。

每加一維，辨識力增加、代價變成 `O(n^(k+1) log n)` 級。一個自然的問題是：有沒有固定的 k 能分開所有圖？**CFI 構造**（Cai、Fürer、Immerman 1992）給了否定答案。這裡只講構造的概念，不給證明：

```text
1. 從一張底圖 B（例如 3-正則 expander）出發。
2. 每個點換成一個 gadget：gadget 的「內部狀態」記錄了一組 XOR 約束（相鄰 gadget 之間的
   連接方式有兩種：直接接，或「扭一下」）。
3. X(B) = 不扭任何邊；X̃(B) = 恰好扭一條邊。
   X 和 X̃ 不同構：扭的總數的奇偶性是全域不變量，換到哪條邊都改不掉。
4. 但要「看見」這個奇偶性，k-WL 必須同時追蹤足夠多的點，多到能把底圖 B 切開；
   底圖的 treewidth（Ch 36）越大，需要的 k 越大。
   取 treewidth 為 Θ(n) 的底圖，就得到需要 k = Ω(n) 的一族圖。
```

結論：**對每個常數 k，都存在 k-WL 分不開的不同構圖對，而且點數只有 O(k)**。WL 階層不會收斂到一個多項式時間的 GI 演算法。CFI 圖的差別本質上是一個線性代數（GF(2) 上的方程組）問題，這也提示了為什麼後來的演算法需要群論。

**Babai（2015）**：[arXiv:1512.03547](https://arxiv.org/abs/1512.03547)，〈Graph Isomorphism in Quasipolynomial Time〉，v1 在 2015 年 12 月 11 日提交、v2 在 2016 年 1 月 19 日修訂，89 頁。主要結果：GI（以及更一般的、在置換群作用下的 String Isomorphism）可以在 `exp((log n)^O(1))` 時間內解決。之前最好的一般界是 Babai–Luks（1983）的 `exp(O(√(n log n)))`。

架構上，它是 Luks（1982）群論方法的延伸：把同構問題化成置換群上的字串同構問題，沿著群的結構遞迴。WL 在裡面的角色是「組合部分」：當群論的遞迴卡在某種高度對稱的結構（Johnson graph）時，用 individualization 加上高維 WL 式的細分把對稱打破（就我的理解，個別化的點數與 WL 的維度都在 `log n` 的多項式量級），這是名為 Split-or-Johnson 的子程序。準多項式時間大致來自「個別化 polylog 個點」產生的 `n^(polylog n)` 個分支。這裡只給架構，細節請讀原文第 1 節的 overview，本章不重述證明。

這個結果有一段值得知道的插曲。2017 年 1 月 4 日，Babai 撤回了準多項式的宣稱，因為 Harald Helfgott 在驗證時發現 Split-or-Johnson 的遞迴分析有錯，誤差會累積；Babai 一度把結果降級為次指數時間。1 月 9 日 Babai 宣布找到修正（讓那個遞迴不再分成兩支），準多項式時間的宣稱恢復，Helfgott 隨後在巴黎的 Bourbaki 研討會上公開確認修正正確。就我查到的資料，arXiv 上的版本停在 2016 年的 v2，修正發布在 Babai 的個人網頁上；讀 arXiv 版時要知道 Split-or-Johnson 那段的分析後來改過。

## 對比與取捨

| 方法 | 分得開什麼 | 代價 | 實務角色 |
| --- | --- | --- | --- |
| 度數序列 | 很少 | `O(n + m)` | 最先跑的過濾 |
| 1-WL（color refinement） | 幾乎所有隨機圖；看不見環長、分不開正則圖 | `O((n + m) log n)`（最佳實作） | 所有 GI 工具的第一步；Ch 40 的特徵 |
| k-WL | 越來越多；CFI 圖需要 `k = Ω(n)` | `O(n^(k+1) log n)` | 理論工具；k = 2、3 偶爾實用 |
| Individualization-refinement（nauty、Traces、bliss） | 全部（完整判定） | 最壞指數（Neuen–Schweitzer 2018 構造了下界），實務很快 | 實際在用的工具 |
| VF2 系列回溯（`nx.is_isomorphic`） | 全部 | 最壞指數 | 小圖、子圖同構 |
| Babai | 全部 | `exp((log n)^O(1))` | 理論；目前沒有實用實作，就我所知 |

## 踩雷集錦

1. **把「1-WL 直方圖相同」當成同構**：它只是必要條件。atlas 裡 7 點以下就有 26 對反例，而且 22 對不是正則圖。拿 WL hash 當「圖的指紋」做去重時，碰撞是會發生的，必須再跑一次真正的同構檢查。
2. **兩張圖分開跑 1-WL 再比顏色編號**：顏色編號依第一次出現的順序給，兩張圖各自編號，「顏色 3」的意義不同。要嘛共用字典（本章做法），要嘛用和順序無關的雜湊（NetworkX 的做法，把 signature 用 blake2b 雜湊成字串）。
3. **以為 1-WL 只在正則圖上失敗**：它看不見環長與部分全域結構，6-cycle 加弦與啞鈴圖這種非正則的小例子就騙得過它。命題 3 只是最容易證明的一類。
4. **跨版本比較 WL hash**：NetworkX 3.7 跑 `weisfeiler_lehman_graph_hash` 時會發出 `UserWarning`：沒有點/邊屬性的圖，hash 值在 v3.5 因為 bug 修正而改變（官方文件說，對無標籤的無向圖，舊版的值相當於新版多跑一輪；有向圖則修正了不分入邊出邊的問題）。如果你把 hash 存進資料庫做函式相似度索引（Ch 40 的情境），升級 NetworkX 後舊索引就全部失效。存 hash 時一併記錄函式庫版本與 `iterations` 參數（預設 3）。
5. **把 nauty 的「實務很快」當成多項式**：Neuen 與 Schweitzer（2018）構造了一族圖，讓所有 individualization-refinement 演算法（不論挑色類的策略、加了什麼不變量）都需要指數時間。實務快是因為那些圖很少自然出現。

## 進階：再往深一層

- **邏輯刻畫**：k-WL 分不開 G、H ⇔ G、H 滿足同樣的「帶計數量詞、至多 k+1 個變數」的一階邏輯句子（Cai–Fürer–Immerman）。1-WL 對應 C²，還等價於「存在雙隨機矩陣 X 使 `AX = XB`」的分數同構（Tinhofer）。這讓 WL 同時連到邏輯、線性規劃（[Ch 26](./26-lp-duality.md)）與譜方法。
- **WL 與 GNN**：message-passing 圖神經網路的辨識力上界恰好是 1-WL（Xu 等人、Morris 等人 2019）。本課不涵蓋 GNN，但如果你之後讀相關論文，這是它們最常引用的定理，而本章的 26 對反例就是那些網路分不開的圖。
- **Canonical labeling**：nauty 不只判定同構，還輸出一個 canonical form：同構的圖得到逐位元相同的標號。用它做大量圖的去重時，canonical form 才是正確的 key，WL hash 不是（踩雷 1）。
- **子圖同構完全是另一回事**：「H 是不是 G 的子圖」是 NP-complete（它包含 clique 與 Hamilton cycle）。Ch 40 的函式比對如果要找「部分相同的程式碼」，問題會從 GI 這種中間地帶掉進 NP-complete。

## 動手練習

1. 在 `wl.py` 加上點標籤（例如 basic block 的指令數）當初始顏色，觀察 C6 與 2×C3 在標籤不全相同時是否變得可區分。這就是 Ch 40 用 opcode 統計當初始顏色的動機。
2. 實作上面描述的二維 WL（給有序點對上色，初始色 = (u = v?, u、v 相鄰?)，每輪收集 `(c(u, w), c(w, v))` 的多重集），驗證它能分開 atlas 裡那 26 對中的哪幾對，並確認它分不開 rook 與 Shrikhande。
3. 統計 `isomorphic_ir` 在 300 組隨機圖對上的分支次數（被呼叫幾次），找出分支最多的那一組，說明它的對稱長什麼樣子。

## 本章重點整理

- GI 在 NP 裡、被認為不是 NP-complete，Babai 2015 給了準多項式演算法（2017 年初修正過一次）。
- 1-WL 用「自己的顏色 + 鄰居顏色多重集」反覆細分，至多 n 輪穩定；直方圖不同 ⇒ 不同構，反之不成立。
- 1-WL 分不開所有同參數的正則圖，也分不開一些非正則的小圖（看不見環長）；individualization + refinement 補成完整判定器，最壞仍是指數。
- CFI 構造證明沒有固定的 k 能讓 k-WL 分開所有圖；Ch 40 拿 WL 當特徵用，要記住它的碰撞與版本問題。

## 自我檢核

- [ ] 不看筆記，能證明「同構 ⇒ 每輪 1-WL 直方圖相同」，並舉出反方向失敗的例子
- [ ] 能解釋為什麼比較兩張圖時必須共用顏色字典
- [ ] 能說明 individualization-refinement 為什麼是完整的判定器，以及它的最壞情況從哪裡來
- [ ] 有人說「Babai 證明了 GI 在 P 裡」，你會怎麼糾正？
- [ ] 想一個你會拿 WL hash 去重的場景（函式、設定檔、依賴圖），碰撞會造成什麼後果？你要怎麼補？

## 延伸閱讀

### 論文

- **[Graph Isomorphism in Quasipolynomial Time](https://arxiv.org/abs/1512.03547)** — László Babai，arXiv:1512.03547（v1 2015-12-11，v2 2016-01-19）；STOC 2016 有延伸摘要版
  - **核心貢獻**：GI 與 String Isomorphism 在 `exp((log n)^O(1))` 時間可解。
  - **讀哪裡**：第 1 節的 overview 與歷史回顧寫得很好，不需要群論背景也能讀；Split-or-Johnson 那幾節在 2017 年修正過，以作者後來的更新為準。
  - **前提**：本章的 WL 與 individualization；置換群的基本概念（軌道、穩定子）。
- **[Graph Isomorphism Vanquished — Again](https://www.quantamagazine.org/graph-isomorphism-vanquished-again-20170114/)** — Erica Klarreich（Quanta Magazine，2017-01-14）
  - **這篇說什麼**：Helfgott 發現錯誤、Babai 撤回、五天後修正、Helfgott 確認的經過，以及錯誤出在哪個子程序。
  - **為什麼值得讀**：本章那段插曲的來源；是少數把一個前沿理論結果的驗證過程寫清楚的科普文章。
- **[An Optimal Lower Bound on the Number of Variables for Graph Identification](https://people.cs.umass.edu/~immerman/pub/opt.pdf)** — Jin-Yi Cai、Martin Fürer、Neil Immerman，Combinatorica 12(4)，pp. 389–410（1992）
  - **核心貢獻**：CFI 構造，證明分開 n 點圖需要 `Ω(n)` 個變數（等價地，k-WL 需要 `k = Ω(n)`），並建立 k-WL 與計數邏輯的等價。
  - **讀哪裡**：gadget 構造那一節與「扭邊」的奇偶論證；邏輯部分（pebble game）可以先跳過。
  - **前提**：本章 k-WL 節、Ch 36 的 treewidth（理解為什麼底圖要「難切」）。
- **[Practical Graph Isomorphism, II](https://arxiv.org/abs/1301.1493)** — Brendan D. McKay、Adolfo Piperno，Journal of Symbolic Computation 60，pp. 94–112（2014）
  - **核心貢獻**：nauty 的最新描述與新工具 Traces；individualization-refinement 框架的完整說明，與 saucy、bliss、conauto 的比較。
  - **讀哪裡**：第 2 節的框架（refinement、target cell、search tree、自同構剪枝），對照本章 `isomorphic_ir` 看缺了什麼。
- **[An Exponential Lower Bound for Individualization-Refinement Algorithms for Graph Isomorphism](https://arxiv.org/abs/1705.03283)** — Daniel Neuen、Pascal Schweitzer，STOC 2018，pp. 138–150
  - **核心貢獻**：構造一族不對稱的圖，讓任何 individualization-refinement 演算法都需要指數時間，不論挑色類的策略或加了什麼不變量。
  - **讀哪裡**：引言就交代了為什麼先前的反例（Miyazaki）不夠，以及這個構造和 CFI 的關係。
- **[Weisfeiler-Lehman Graph Kernels](https://www.jmlr.org/papers/v12/shervashidze11a.html)** — Nino Shervashidze、Pascal Schweitzer、Erik Jan van Leeuwen、Kurt Mehlhorn、Karsten M. Borgwardt，JMLR 12，pp. 2539–2561（2011）
  - **核心貢獻**：把 WL 每一輪的顏色當特徵，定義圖之間的 kernel，時間線性於邊數與輪數。
  - **讀哪裡**：WL subtree kernel 那一節。這是 [Ch 40](./40-binary-similarity.md) 把函式 CFG 變成向量的直接前身。

### 官方文件

- **[NetworkX：weisfeiler_lehman_graph_hash](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.graph_hashing.weisfeiler_lehman_graph_hash.html)**
  - **讀哪裡**：參數 `iterations`（預設 3）、`digest_size`（預設 16 bytes）、`node_attr`；以及 v3.5 hash 值改變的警告說明。存 hash 之前一定要讀。

Part 6 到這裡結束：我們知道了哪些圖問題難（Ch 34）、怎麼把難題交給 SAT（Ch 35）、怎麼利用結構讓難題變簡單（Ch 36），以及一個處在中間地帶的問題怎麼被實務解決（Ch 37）。Part 7 把這些工具帶進真實的程式分析，第一站是程式本身的圖。

→ [Ch 38 程式分析的圖：CPG 與 reachability](./38-program-analysis-graphs.md)
