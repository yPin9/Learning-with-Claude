# Ch 7 — 圖的定義、表示與同構

> **目標**：把「圖」從刷題時的一個 `adj` 變數，升級成可以下定義、可以證明性質的數學物件；能判斷一串數字能不能當某張圖的度數序列（Havel–Hakimi、Erdős–Gallai），能證明「二分圖 ⇔ 沒有奇圈」並寫出會交出反例證據的判定程式，也知道「兩張圖一樣嗎」為什麼是個困難問題。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

你寫過一百次 BFS，但如果有人問「`adj` 裡可不可以有 `adj[v]` 包含 `v` 自己」「兩條平行邊算一條還是兩條」「`dict` 的 key 換個名字還是同一張圖嗎」，多數人的答案是「看題目」。刷題可以看題目；證明不行。Part 2 接下來的每一個定理——Euler、Menger、dominator——都建立在一組精確的定義上，定義差一點，定理就從對變成錯。例如「每個點度數都是偶數 ⇒ 有 Euler 迴路」在允許平行邊的多重圖上成立，但你若把「連通」漏掉，它就是錯的。

歷史上圖論的術語在 20 世紀初各說各話，直到 König 1936 年那本教科書和後來 Harary、Berge 的書才逐漸統一；到今天，「graph」在不同書裡仍可能指簡單圖或多重圖。這章做三件事：

1. 把詞彙釘死，並說明本課預設哪一種。
2. 回答兩個「存在性」問題：給一串度數，存在這樣的圖嗎？給一張圖，它能二著色嗎？兩個問題都有漂亮的「有就給構造、沒有就給證據」的答案。
3. 面對「同構」：什麼叫兩張圖一樣，為什麼這個問題到今天都沒有已知的多項式演算法。

## 先建立直覺

把圖想成一份「誰認識誰」的名單。名單上的人名（節點）只是標籤，真正的資訊是**誰和誰之間有線**。把所有人的名字換掉，社交結構沒變——這就是「同構」的直覺。把每個人認識幾個人抄成一串數字，這是「度數序列」；它丟掉了很多資訊，所以不同的圖可以有同一串度數，但它也保留了一些硬約束（例如總和必須是偶數）。

```text
   a ─── b          邊列表   : ab, ac, bc, cd
   │   ╱            鄰接字典 : a:{b,c}  b:{a,c}  c:{a,b,d}  d:{c}
   │  ╱             度數     : a=2 b=2 c=3 d=1
   c ─── d          Σdeg = 8 = 2·|E|
```

> 如果你對「歸納法、反證法、極端原理」還不熟，先回看 [Ch 2 — 證明工具箱](./02-proof-toolbox.md)。這章的 Havel–Hakimi 證明是極端原理（選一個「最好的」實現）的標準示範。

## 定義：把詞彙釘死

**圖（graph）** `G = (V, E)`：`V` 是有限節點集合，`E` 是邊集合。依邊長什麼樣子分成：

| 名稱 | 邊是什麼 | 允許自環（loop） | 允許平行邊 | 本課在哪用 |
| --- | --- | --- | --- | --- |
| 簡單圖（simple graph） | 兩個**相異**點的無序對 `{u, v}` | 否 | 否 | 預設；沒特別說就是它 |
| 多重圖（multigraph） | 邊有自己的名字，端點可重複 | 是 | 是 | Ch 8 Euler（七橋就是多重圖） |
| 有向圖（digraph） | 有序對 `(u, v)` | 視書而定 | 否 | Ch 13、Ch 14 |
| 加權圖（weighted graph） | 邊上多一個數 `w(u, v)` | 否 | 否 | Ch 10 MST |

幾個每章都會用的詞：

- **相鄰（adjacent）**：`{u, v} ∈ E`，寫 `u ~ v`。`v` 的**鄰居集合** `N(v) = {u : u ~ v}`。
- **度數（degree）** `deg(v) = |N(v)|`（多重圖裡自環算 2）。最小度數 `δ(G)`、最大度數 `Δ(G)`。
- **路徑（path）**：節點互不重複的序列 `v0 v1 … vk`，相鄰兩點有邊，長度 `k`。**走（walk）**允許重複點與重複邊；**跡（trail）**允許重複點、不允許重複邊。這三個詞在 Ch 8 會變得很重要：Euler 談的是跡，Hamilton 談的是路徑。
- **圈（cycle）**：`k ≥ 3` 個相異點 `v1 … vk`，`vk ~ v1`，長度 `k`。長度是奇數就叫奇圈。
- **連通（connected）**：任兩點間有路徑。極大的連通子圖叫**連通分量（component）**。

NetworkX 的對應：`nx.Graph` 是簡單圖（會默默吃掉平行邊，但**允許**自環——這是一個踩雷點，見後面），`nx.MultiGraph` 是多重圖，`nx.DiGraph` 是有向圖。

### 握手引理

**定理（握手引理）**：任何圖都有 `Σ deg(v) = 2|E|`。

**證明**：對「（點，邊）且點是邊的端點」這種配對做雙重計數。依點來數，點 `v` 貢獻 `deg(v)` 個配對；依邊來數，每條邊恰有兩個端點（自環在度數定義裡算 2 次，所以也是 2）。兩種數法數的是同一個集合，所以相等。∎

**推論**：奇數度的點有偶數個。因為 `Σ deg(v)` 是偶數，把偶數度的點拿掉後剩下的和仍是偶數，而一堆奇數加起來是偶數，個數必為偶數。這個推論是 Ch 8 Euler 定理的伏筆：七橋的四塊陸地度數是 5、3、3、3，四個奇點。

有向圖的版本：`Σ out-deg(v) = Σ in-deg(v) = |E|`，每條有向邊恰好貢獻一個出度和一個入度。

## 三種表示，各有用途

```python
import networkx as nx

# 同一張圖的三種表示：邊列表、鄰接字典、鄰接矩陣
edges = [("a", "b"), ("a", "c"), ("b", "c"), ("c", "d")]

adj = {}
for u, v in edges:
    adj.setdefault(u, set()).add(v)
    adj.setdefault(v, set()).add(u)

nodes = sorted(adj)
idx = {v: i for i, v in enumerate(nodes)}
A = [[0] * len(nodes) for _ in nodes]
for u, v in edges:
    A[idx[u]][idx[v]] = A[idx[v]][idx[u]] = 1

deg = {v: len(adj[v]) for v in nodes}
print("adj   :", {v: sorted(adj[v]) for v in nodes})
print("matrix:", A)
print("deg   :", deg)
print("Σdeg =", sum(deg.values()), " 2|E| =", 2 * len(edges))

# 和 NetworkX 對答案
G = nx.Graph(edges)
assert {u: set(G[u]) for u in G} == adj
assert dict(G.degree()) == deg
print("NetworkX 一致")
```

```text
adj   : {'a': ['b', 'c'], 'b': ['a', 'c'], 'c': ['a', 'b', 'd'], 'd': ['c']}
matrix: [[0, 1, 1, 0], [1, 0, 1, 0], [1, 1, 0, 1], [0, 0, 1, 0]]
deg   : {'a': 2, 'b': 2, 'c': 3, 'd': 1}
Σdeg = 8  2|E| = 8
NetworkX 一致
```

本課自刻演算法一律用鄰接字典 `adj: dict[node, set[node]]`。為什麼不選另外兩種：

| 表示 | 空間 | 「`u ~ v`？」 | 列出 `N(v)` | 適合 |
| --- | --- | --- | --- | --- |
| 邊列表 | `O(m)` | `O(m)` | `O(m)` | 輸入格式、Kruskal（Ch 10 要排序邊） |
| 鄰接矩陣 | `O(n²)` | `O(1)` | `O(n)` | 稠密圖、譜方法（Part 5 的特徵值） |
| 鄰接字典（set） | `O(n + m)` | 平均 `O(1)` | `O(deg v)` | 本課預設：稀疏圖、DFS/BFS 類演算法 |
| CSR（壓縮列） | `O(n + m)` | `O(log deg)` | `O(deg v)`，cache 友善 | 大圖，見 Ch 41 |

`O(n + m)` 裡的 `n = |V|`、`m = |E|`，這兩個符號全課通用。矩陣在 `n = 10⁶` 的稀疏圖上要 `10¹²` 格，放不進記憶體；字典在需要矩陣代數時又很難用。表示法不是品味問題，是演算法的時間複雜度問題。

## 度數序列：一串數字能不能變成一張圖？

**問題**：給 `d = (d1, …, dn)`，存在簡單圖使第 `i` 個點度數是 `di` 嗎？存在的話稱 `d` **可圖（graphic）**。

必要條件立刻有兩個：總和是偶數（握手引理）、每個 `di ≤ n − 1`（簡單圖最多連到其他 `n−1` 個點）。但不充分：`(3, 3, 1, 1)` 總和 8、最大值 3 = n−1，可是兩個度數 3 的點都要連到所有其他點，那兩個度數 1 的點就各有兩個鄰居了，矛盾。

### Havel–Hakimi 定理

把 `d` 排成非遞增 `d1 ≥ d2 ≥ … ≥ dn`。定義 `d'`：刪掉 `d1`，把接下來 `d1` 個數各減 1，其餘不動：

```text
d  = (d1, d2, …, d_{d1+1}, d_{d1+2}, …, dn)
d' = (    d2−1, …, d_{d1+1}−1, d_{d1+2}, …, dn)
```

**定理（Havel 1955；Hakimi 1962 獨立發現）**：`d` 可圖 ⇔ `d'` 可圖。

**證明（⇐）**：取 `d'` 的一個實現（realization，指度數恰為 `d'` 的簡單圖），新加一個點 `v1`，連到度數為 `d2−1, …, d_{d1+1}−1` 的那 `d1` 個點。那些點度數各加 1，`v1` 度數是 `d1`，得到 `d` 的實現。

**證明（⇒）**：設 `d` 可圖。在所有 `d` 的實現中（點 `vi` 的度數是 `di`），令 `T = {v2, …, v_{d1+1}}`（「前 `d1` 名」），**選一個讓 `v1` 在 `T` 裡的鄰居數最多的實現 `G`**（極端原理）。我們證明在 `G` 中 `N(v1) = T`。

反設不然。`|N(v1)| = d1 = |T|`，所以存在 `vi ∈ T` 不和 `v1` 相鄰，也存在 `vj ∉ T`（`j > d1 + 1`）和 `v1` 相鄰。因為 `i < j`，`di ≥ dj`。

看 `A = N(vi) \ {vj}` 和 `B = N(vj) \ {vi}`。兩者大小分別是 `di − [vi~vj]` 和 `dj − [vi~vj]`（`[·]` 在成立時為 1），所以 `|A| ≥ |B|`。又 `v1 ∈ B` 但 `v1 ∉ A`（`v1` 不和 `vi` 相鄰）。若 `A ⊆ B`，則 `A ⊆ B \ {v1}`，`|A| ≤ |B| − 1`，矛盾。所以存在 `w ∈ A \ B`：`w ~ vi`、`w ≁ vj`、`w ≠ vj`，且 `w ≠ v1`。

做一次 **2-交換（2-switch）**：刪掉 `v1vj` 和 `vi w`，加上 `v1vi` 和 `vj w`。

```text
   v1 ── vj            v1    vj
                  ⟹    │      │
   vi ── w             vi     w
```

四個點的度數都不變；新加的兩條邊原本都不存在（`v1 ≁ vi`、`vj ≁ w`），所以仍是簡單圖。但 `v1` 在 `T` 裡的鄰居多了 `vi`、少的 `vj` 不在 `T` 裡——鄰居數增加 1，和 `G` 的選法矛盾。所以 `N(v1) = T`，把 `v1` 刪掉，剩下的圖的度數序列正好是 `d'`。∎

這個證明直接給出演算法：反覆「取最大度數點，連到接下來度數最大的那幾個」，失敗就代表不可圖。

```python
import networkx as nx


def havel_hakimi_build(degrees):
    """degrees: dict 節點 -> 期望度數。成功回傳邊集合，失敗回傳 None。"""
    need = dict(degrees)
    edges = set()
    while True:
        alive = sorted((v for v in need if need[v] > 0), key=lambda v: (-need[v], v))
        if not alive:
            return edges
        v = alive[0]
        d = need[v]
        others = alive[1:]
        print(f"  取 {v}(需 {d})，接到 {others[:d]}")
        if d > len(others):
            return None
        for u in others[:d]:
            edges.add(tuple(sorted((u, v))))
            need[u] -= 1
        need[v] = 0


want = {"a": 4, "b": 4, "c": 3, "d": 3, "e": 2, "f": 2}
E = havel_hakimi_build(want)
print("edges:", sorted(E))
G = nx.Graph(sorted(E))
print("實際度數:", dict(sorted(G.degree())))
assert dict(G.degree()) == want

print("失敗例：")
print(havel_hakimi_build({"a": 3, "b": 3, "c": 1, "d": 1}))
```

```text
  取 a(需 4)，接到 ['b', 'c', 'd', 'e']
  取 b(需 3)，接到 ['c', 'd', 'f']
  取 c(需 1)，接到 ['d']
  取 e(需 1)，接到 ['f']
edges: [('a', 'b'), ('a', 'c'), ('a', 'd'), ('a', 'e'), ('b', 'c'), ('b', 'd'), ('b', 'f'), ('c', 'd'), ('e', 'f')]
實際度數: {'a': 4, 'b': 4, 'c': 3, 'd': 3, 'e': 2, 'f': 2}
失敗例：
  取 a(需 3)，接到 ['b', 'c', 'd']
  取 b(需 2)，接到 []
None
```

失敗例正好是前面手算的 `(3, 3, 1, 1)`：`a` 吃光了所有人的度數，`b` 還欠 2 卻沒有人可以接。注意 `alive` 只保留還需要度數的點——`c`、`d` 已經被 `a` 填滿了。

### Erdős–Gallai 定理

Havel–Hakimi 是一個「過程」；Erdős–Gallai 是一組「不等式」，不用模擬就能檢查。

**定理（Erdős–Gallai 1960）**：非遞增、總和為偶數的非負整數序列 `d1 ≥ … ≥ dn` 可圖 ⇔ 對每個 `k = 1, …, n`：

```text
d1 + … + dk  ≤  k(k−1) + Σ_{i>k} min(di, k)
```

**必要性證明**：取一個實現，令 `S = {v1, …, vk}`。左式 `Σ_{i≤k} di` 數的是「從 `S` 裡的點伸出去的邊端點數」。這些端點分兩類：

- 兩端都在 `S` 內的邊，每條被數 2 次；`S` 內最多 `C(k,2)` 條邊，貢獻至多 `2·C(k,2) = k(k−1)`。
- 一端在 `S`、一端是 `S` 外的 `vi` 的邊：`vi` 最多連到 `S` 的全部 `k` 個點，也最多用掉自己的 `di` 個度數，所以貢獻至多 `min(di, k)`。

兩類加起來就是右式。∎

**充分性**：這個方向難得多，這裡只給證明架構，**不證**。標準作法是對 `Σ di` 或 `n` 做歸納：證明不等式組在 Havel–Hakimi 的那一步「刪最大、前 `d1` 個減 1」之後仍然成立，然後套用 Havel–Hakimi 定理。Choudum 1986 給了一頁長的歸納證明，延伸閱讀有列。我們用暴力窮舉代替：

```python
from itertools import combinations_with_replacement
import networkx as nx


def havel_hakimi(seq):
    """回傳 True/False：seq 是否為某個簡單圖的度數序列。"""
    s = sorted(seq, reverse=True)
    while s and s[0] > 0:
        d = s.pop(0)                 # 取最大度數 d
        if d > len(s):
            return False             # 不夠鄰居可接
        for i in range(d):           # 接到接下來 d 個最大的
            s[i] -= 1
            if s[i] < 0:
                return False
        s.sort(reverse=True)         # 每輪都要重新排序
    return all(x == 0 for x in s)


def erdos_gallai(seq):
    d = sorted(seq, reverse=True)
    n = len(d)
    if sum(d) % 2 or any(x < 0 for x in d):
        return False
    for k in range(1, n + 1):
        if sum(d[:k]) > k * (k - 1) + sum(min(x, k) for x in d[k:]):
            return False
    return True


# 標準答案：graph atlas 收錄 ≤7 點的所有同構類，收集它們的度數序列
truth = {}
for G in nx.graph_atlas_g():
    n = G.number_of_nodes()
    truth.setdefault(n, set()).add(tuple(sorted((d for _, d in G.degree()), reverse=True)))

for n in range(1, 8):
    # 候選：所有長度 n、每項在 0..n-1 的非遞增序列
    cands = [tuple(sorted(c, reverse=True))
             for c in combinations_with_replacement(range(n), n)]
    hh = {c for c in cands if havel_hakimi(c)}
    eg = {c for c in cands if erdos_gallai(c)}
    nxg = {c for c in cands if nx.is_graphical(list(c))}
    assert hh == eg == nxg == truth[n], n
    print(f"n={n}: 候選 {len(cands):4d} 個，可圖 {len(hh):3d} 個，HH/EG/NetworkX/atlas 全部一致")

print(havel_hakimi([3, 3, 3, 3]), havel_hakimi([3, 3, 1, 1]), havel_hakimi([4, 4, 4, 1, 1]))
```

```text
n=1: 候選    1 個，可圖   1 個，HH/EG/NetworkX/atlas 全部一致
n=2: 候選    3 個，可圖   2 個，HH/EG/NetworkX/atlas 全部一致
n=3: 候選   10 個，可圖   4 個，HH/EG/NetworkX/atlas 全部一致
n=4: 候選   35 個，可圖  11 個，HH/EG/NetworkX/atlas 全部一致
n=5: 候選  126 個，可圖  31 個，HH/EG/NetworkX/atlas 全部一致
n=6: 候選  462 個，可圖 102 個，HH/EG/NetworkX/atlas 全部一致
n=7: 候選 1716 個，可圖 342 個，HH/EG/NetworkX/atlas 全部一致
True False False
```

「標準答案」從哪來：`nx.graph_atlas_g()` 收錄了 0 到 7 個點的**每一個**同構類各一張（共 1253 張），度數序列是同構不變量，所以把它們的度數序列收集起來，就是所有可圖序列。我們讓三種方法（自刻 HH、自刻 EG、`nx.is_graphical`）和這份清單四方對拍。`(4, 4, 4, 1, 1)` 總和 14 是偶數、最大值 4 = n−1，仍然不可圖——用 `k = 3` 檢查：左式 12，右式 `3·2 + min(1,3) + min(1,3) = 8`，不等式失敗。這就是 Erdős–Gallai 比「總和偶數 + 最大值 ≤ n−1」強的地方：它告訴你**哪一群點**撐不起來。

## 子圖與補圖

- **子圖（subgraph）** `H ⊆ G`：`V(H) ⊆ V(G)`、`E(H) ⊆ E(G)`，且 `H` 的邊端點都在 `V(H)` 裡。
- **誘導子圖（induced subgraph）** `G[S]`：取點集 `S`，**`G` 在 `S` 內的所有邊都要**。「`G` 包含三角形當子圖」和「當誘導子圖」等價，但「`G` 包含 `C4` 當誘導子圖」比「當子圖」強很多——Ch 16 的 chordal graph 就定義成「沒有長度 ≥ 4 的**誘導**圈」。
- **生成子圖（spanning subgraph）**：`V(H) = V(G)`，只刪邊。生成樹（Ch 9、Ch 10）就是連通、無圈的生成子圖。
- **補圖（complement）** `Ḡ`：同一個點集，`u ~ v` 在 `Ḡ` 中 ⇔ `u ≁ v` 在 `G` 中。`|E(Ḡ)| = C(n,2) − |E(G)|`。

補圖不只是定義練習：「`G` 裡的獨立集」就是「`Ḡ` 裡的團（clique）」，Ch 34 用這個關係在兩個 NP-complete 問題之間做歸約。和自己的補圖同構的圖叫**自補圖（self-complementary）**：

```python
from itertools import permutations
import networkx as nx


def complement(adj):
    V = set(adj)
    return {v: V - adj[v] - {v} for v in adj}


def edge_set(adj):
    return {frozenset((u, v)) for u in adj for v in adj[u]}


def isomorphic_brute(a1, a2):
    """暴力試所有 n! 個雙射；回傳一個同構映射或 None。"""
    if len(a1) != len(a2):
        return None
    V1, V2 = sorted(a1), sorted(a2)
    E1, E2 = edge_set(a1), edge_set(a2)
    if len(E1) != len(E2):
        return None
    for perm in permutations(V2):
        f = dict(zip(V1, perm))
        if all(frozenset((f[u], f[v])) in E2 for u, v in map(tuple, E1)):
            return f
    return None


P4 = {0: {1}, 1: {0, 2}, 2: {1, 3}, 3: {2}}
C5 = {i: {(i - 1) % 5, (i + 1) % 5} for i in range(5)}
for name, g in [("P4", P4), ("C5", C5)]:
    f = isomorphic_brute(g, complement(g))
    print(f"{name} 與補圖同構？ {f is not None}，映射 {f}")

# 用 NetworkX 的 graph atlas（所有 ≤7 點的圖，各同構類一張）數自補圖
cnt = {}
for G in nx.graph_atlas_g():
    n = G.number_of_nodes()
    if n and nx.is_isomorphic(G, nx.complement(G)):
        cnt[n] = cnt.get(n, 0) + 1
print("自補圖個數（同構意義下）:", cnt)
```

```text
P4 與補圖同構？ True，映射 {0: 1, 1: 3, 2: 0, 3: 2}
C5 與補圖同構？ True，映射 {0: 0, 1: 2, 2: 4, 3: 1, 4: 3}
自補圖個數（同構意義下）: {1: 1, 4: 1, 5: 2}
```

自補圖的邊數必須等於補圖的邊數，也就是 `C(n,2)/2`，所以 `C(n,2)` 要是偶數，即 `n ≡ 0, 1 (mod 4)`；`n = 2, 3, 6, 7` 數到 0 個，和必要條件吻合。atlas 只到 7 點，所以 `n = 8` 的自補圖沒出現在結果裡。`C5` 的映射 `i ↦ 2i mod 5` 把「相差 1」變成「相差 2」，正好把邊換成非邊。

## 二分圖與奇圈定理

**二分圖（bipartite graph）**：`V` 可以分成兩塊 `X ∪ Y`，每條邊一端在 `X`、一端在 `Y`。等價說法：可以用兩種顏色給點著色，使相鄰點不同色。

工程上你天天碰到它：使用者–商品、任務–機器、Ch 22 的匹配問題全是二分圖。而判斷一張圖是不是二分圖，有一個「有就給著色、沒有就給證據」的完整刻畫。

**定理（König 1936）**：圖 `G` 是二分圖 ⇔ `G` 沒有奇圈。

**證明（⇒）**：設 `G` 的二著色是 `X ∪ Y`。沿著任何圈 `v1 v2 … vk v1` 走，每走一步顏色就換一次；走回 `v1` 時顏色要和出發時相同，所以換色次數 `k` 是偶數。

**證明（⇐）**：設 `G` 沒有奇圈。各連通分量分開著色互不影響，所以假設 `G` 連通。固定一點 `r`，對每個點做 BFS 得到距離 `dist(v)` 與 BFS 樹的父節點 `parent(v)`。令 `X = {v : dist(v) 偶}`、`Y = {v : dist(v) 奇}`。要證每條邊兩端不同色。

反設有邊 `uw`，`dist(u)` 和 `dist(w)` 同奇偶。相鄰兩點的距離最多差 1（從 `r` 到 `u` 的最短路再走一步就到 `w`），同奇偶又最多差 1，所以 `dist(u) = dist(w) = t`。

在 BFS 樹上，從 `u` 和 `w` 一起往上爬到它們的最近共同祖先 `x`，設 `dist(x) = s`。`u ≠ w` 且深度相同，所以兩者都不是對方的祖先，`s < t`。樹路徑 `x → u` 長 `t − s`、`x → w` 長 `t − s`，兩條路徑除了 `x` 之外不共點（`x` 是**最近**共同祖先）。把它們和邊 `uw` 接起來得到一個圈，長度 `2(t − s) + 1`，是奇數，矛盾。∎

這個證明是構造性的：BFS 失敗時，我們手上真的有一個奇圈。下面的程式把它交出來，而不是只回傳 `False`：

```python
from collections import deque
import random
import networkx as nx


def bipartition_or_odd_cycle(adj):
    """回傳 ("bipartite", color) 或 ("odd_cycle", [v0, v1, ..., vk])。"""
    color, parent, depth = {}, {}, {}
    for s in sorted(adj):
        if s in color:
            continue
        color[s], parent[s], depth[s] = 0, None, 0
        q = deque([s])
        while q:
            u = q.popleft()
            for w in sorted(adj[u]):
                if w not in color:
                    color[w], parent[w], depth[w] = 1 - color[u], u, depth[u] + 1
                    q.append(w)
                elif color[w] == color[u]:
                    return "odd_cycle", odd_cycle(u, w, parent, depth)
    return "bipartite", color


def odd_cycle(u, w, parent, depth):
    # u、w 同色又相鄰 → BFS 深度同奇偶。兩邊往上爬到最近共同祖先。
    left, right = [u], [w]
    while u != w:
        if depth[u] >= depth[w]:
            u = parent[u]
            left.append(u)
        else:
            w = parent[w]
            right.append(w)
    # left = [u0 ... lca]，right = [w0 ... lca]
    return left[::-1] + right[:-1]


def is_odd_cycle(adj, cyc):
    k = len(cyc)
    return (k % 2 == 1 and len(set(cyc)) == k
            and all(cyc[(i + 1) % k] in adj[cyc[i]] for i in range(k)))


C6 = {i: {(i - 1) % 6, (i + 1) % 6} for i in range(6)}
C7 = {i: {(i - 1) % 7, (i + 1) % 7} for i in range(7)}
kind, col = bipartition_or_odd_cycle(C6)
print(kind, dict(sorted(col.items())))
print(bipartition_or_odd_cycle(C7))

# 對拍：隨機圖，seed 固定
rng = random.Random(7)
n_bip = 0
for t in range(2000):
    n = rng.randint(1, 12)
    p = rng.choice([0.1, 0.2, 0.3, 0.5])
    G = nx.gnp_random_graph(n, p, seed=rng.randint(0, 10**9))
    adj = {v: set(G[v]) for v in G}
    kind, cert = bipartition_or_odd_cycle(adj)
    assert (kind == "bipartite") == nx.is_bipartite(G)
    if kind == "bipartite":
        n_bip += 1
        assert all(cert[u] != cert[v] for u, v in G.edges())
    else:
        assert is_odd_cycle(adj, cert), cert
print(f"2000 張隨機圖全部一致（其中二分圖 {n_bip} 張），每個奇圈證據都驗過")
```

```text
bipartite {0: 0, 1: 1, 2: 0, 3: 1, 4: 0, 5: 1}
('odd_cycle', [0, 1, 2, 3, 4, 5, 6])
2000 張隨機圖全部一致（其中二分圖 1303 張），每個奇圈證據都驗過
```

在 BFS 裡，衝突邊 `uw` 兩端的深度一定相同——這正是證明裡「相鄰點距離最多差 1、又同奇偶」那一步。`odd_cycle` 仍然用 `depth[u] >= depth[w]` 判斷誰先爬，而不是兩邊同步爬，是為了讓函式在你拿去改成 DFS 版（深度可以差很多）時也不會壞掉。程式驗的是**證據本身**（`is_odd_cycle`），而不是相信函式回傳的標籤——這是「可驗證輸出（certifying algorithm）」的想法：演算法除了答案，還交出一個讓別人能在線性時間內檢查的證據。

## 同構：兩張圖什麼時候「一樣」？

**定義**：`G` 與 `H` **同構（isomorphic）**，記作 `G ≅ H`，若存在雙射 `f: V(G) → V(H)` 使得 `u ~ v` 在 `G` ⇔ `f(u) ~ f(v)` 在 `H`。

**圖不變量（graph invariant）**：任何在同構下不變的量——點數、邊數、度數序列、三角形個數、直徑、連通分量數、鄰接矩陣的特徵值（Part 5）。用法是**單向的**：不變量不同 ⇒ 不同構；不變量相同**什麼也不能推出**。

下面兩棵樹度數序列一樣、直徑一樣，但不同構；接著是兩張特徵值完全一樣（同譜，cospectral）但不同構的圖：

```python
import networkx as nx

# 同度數序列 (3,2,2,1,1,1) 的兩棵樹
T1 = nx.Graph([(0, 1), (1, 2), (1, 3), (2, 4), (3, 5)])
T2 = nx.Graph([(0, 4), (0, 5), (1, 3), (2, 3), (3, 4)])

for name, T in [("T1", T1), ("T2", T2)]:
    degs = sorted((d for _, d in T.degree()), reverse=True)
    hub = max(T, key=T.degree)
    around = sorted(T.degree(w) for w in T[hub])
    print(f"{name}: 度數序列 {degs}，直徑 {nx.diameter(T)}，度 3 點的鄰居度數 {around}")
print("同構？", nx.is_isomorphic(T1, T2))

# 同譜但不同構：C4 加一個孤立點 vs 星形 K_{1,4}
A = nx.cycle_graph(4)
A.add_node(4)
B = nx.star_graph(4)
for name, G in [("C4+K1", A), ("K1,4", B)]:
    ev = sorted(float(round(x.real, 6)) + 0.0 for x in nx.adjacency_spectrum(G))
    print(f"{name}: 特徵值 {ev}，度數序列 {sorted(d for _, d in G.degree())}")
```

```text
T1: 度數序列 [3, 2, 2, 1, 1, 1]，直徑 4，度 3 點的鄰居度數 [1, 2, 2]
T2: 度數序列 [3, 2, 2, 1, 1, 1]，直徑 4，度 3 點的鄰居度數 [1, 1, 2]
同構？ False
C4+K1: 特徵值 [-2.0, 0.0, 0.0, 0.0, 2.0]，度數序列 [0, 2, 2, 2, 2]
K1,4: 特徵值 [-2.0, 0.0, 0.0, 0.0, 2.0]，度數序列 [1, 1, 1, 1, 4]
```

`T1` 和 `T2` 的差別藏在一個「組合起來」的不變量裡：度數 3 的那個點，鄰居的度數多重集合不同。這正是 Weisfeiler–Leman 演算法（Ch 37）的核心想法：把「我的度數」精煉成「我的度數 + 鄰居度數的多重集合」，反覆做。

### 不變量能分多細？

`n` 個點的標記圖有 `2^C(n,2)` 張，同構類卻少得多：`n = 1…7` 依序是 1、2、4、11、34、156、1044 類（graph atlas 收錄的正是這些）。同構類數大約是標記圖數除以 `n!`，因為多數圖沒有非平凡的對稱；Ch 6 的 Burnside 引理就是在精確計算這個「除以群的大小」該怎麼修正。

下面把 6 點與 7 點的所有同構類依各種不變量分桶，數有多少類別「撞桶」（和別的類共享同一個不變量值，所以單靠這個不變量分不開）：

```python
from collections import defaultdict
import networkx as nx
import numpy as np
import warnings

warnings.filterwarnings("ignore", category=UserWarning)  # WL hash 的版本變更提示


def invariants(G):
    degs = tuple(sorted((d for _, d in G.degree()), reverse=True))
    tri = sum(nx.triangles(G).values()) // 3
    spec = tuple(np.round(np.linalg.eigvalsh(nx.to_numpy_array(G)), 6) + 0.0)
    return {
        "度數序列": degs,
        "度數序列+三角形數": (degs, tri),
        "鄰接矩陣特徵值": spec,
        "WL hash (3 輪)": nx.weisfeiler_lehman_graph_hash(G, iterations=3),
    }


for n in (6, 7):
    graphs = [G for G in nx.graph_atlas_g() if G.number_of_nodes() == n]
    print(f"n={n}：共 {len(graphs)} 個同構類")
    for name in invariants(graphs[0]):
        buckets = defaultdict(int)
        for G in graphs:
            buckets[invariants(G)[name]] += 1
        collide = sum(c for c in buckets.values() if c > 1)
        print(f"  分出 {len(buckets):4d} 組、撞在一起的類別 {collide:3d} 個 ← {name}")
```

```text
n=6：共 156 個同構類
  分出  102 組、撞在一起的類別  84 個 ← 度數序列
  分出  136 組、撞在一起的類別  40 個 ← 度數序列+三角形數
  分出  151 組、撞在一起的類別  10 個 ← 鄰接矩陣特徵值
  分出  152 組、撞在一起的類別   8 個 ← WL hash (3 輪)
n=7：共 1044 個同構類
  分出  342 組、撞在一起的類別 874 個 ← 度數序列
  分出  622 組、撞在一起的類別 634 個 ← 度數序列+三角形數
  分出  988 組、撞在一起的類別 110 個 ← 鄰接矩陣特徵值
  分出 1018 組、撞在一起的類別  52 個 ← WL hash (3 輪)
```

兩個交叉驗證：`n = 6` 的度數序列分出 102 組，正好等於前面 Erdős–Gallai 那段數到的 102 個可圖序列（每個可圖序列至少對應一個同構類，反之每個同構類有唯一度數序列）；`n = 7` 的 342 也是 7 點可圖序列的個數。結論很清楚：**沒有一個便宜的不變量能完全分開所有圖**，連 WL 在 7 個點就有 52 個類別分不開。特徵值用 `round(…, 6)` 分桶，理論上可能因浮點誤差把同譜的圖切到兩桶；這裡只當觀察用，不是嚴格的同譜判定。

### 同構問題的計算地位

圖同構（Graph Isomorphism, GI）的現況值得記住，因為它是複雜度理論裡少見的「懸案」：

- GI 在 NP 裡（同構映射本身就是可以快速檢查的證據），但**不知道**是否 NP-complete，也**不知道**是否在 P 裡。
- Babai 在 2015 年公布、2016 年 STOC 發表的演算法把它放進 quasi-polynomial time，也就是 `exp((log n)^O(1))`。
- 實務上的工具（nauty/Traces、VF2——`nx.is_isomorphic` 預設用 VF2 系列）在絕大多數圖上很快，但都有已知的困難輸入。

Ch 37 會回來處理 WL 演算法本身與它分不開哪些圖。

## 視覺化：自己畫出來

```python
import matplotlib.pyplot as plt
import networkx as nx

T1 = nx.Graph([(0, 1), (1, 2), (1, 3), (2, 4), (3, 5)])
T2 = nx.Graph([(0, 4), (0, 5), (1, 3), (2, 3), (3, 4)])
fig, axes = plt.subplots(1, 2, figsize=(8, 3))
for ax, (name, T) in zip(axes, [("T1", T1), ("T2", T2)]):
    nx.draw(T, pos=nx.kamada_kawai_layout(T), ax=ax, with_labels=True,
            node_color=["tab:red" if T.degree(v) == 3 else "tab:blue" for v in T])
    ax.set_title(name)
plt.tight_layout()
plt.show()
```

你會看到兩棵六點樹，紅色是度數 3 的點：左邊紅點伸出一根短腳、兩根長度 2 的腳；右邊紅點伸出兩根短腳和一根長度 3 的腳。肉眼一看就知道不同構——但「肉眼一看」正是我們沒辦法寫成快速演算法的東西。

## 對比與取捨

| 問題 | 方法 | 輸出 | 時間 | 證據 |
| --- | --- | --- | --- | --- |
| 序列可圖？ | Havel–Hakimi | 是／否 + 一個實現 | `O(n²)`（每輪排序可以用 bucket 優化） | 是：給圖；否：卡住的那一步 |
| 序列可圖？ | Erdős–Gallai | 是／否 | 排序後 `O(n)` 可做到（前綴和） | 否：違反不等式的 `k` |
| 二分圖？ | BFS 二著色 | 著色或奇圈 | `O(n + m)` | 兩個方向都有 |
| 同構？ | 暴力 `n!` | 映射或否 | `O(n!·m)` | 是：映射；否：無簡短證據 |
| 同構？ | 不變量比較 | 「不同構」或「不知道」 | 看不變量 | 只有否方向 |

最後一列是同構之所以難的直覺：「同構」有短證據（映射），「不同構」一般而言沒有已知的短證據。

## 踩雷集錦

1. **以為 `nx.Graph` 是嚴格的簡單圖**：`nx.Graph` 會把重複加入的邊合併，但 `G.add_edge(1, 1)` 會成功加入自環，而且 `G.degree(1)` 會把它算成 2。從資料匯入邊時，先 `G.remove_edges_from(nx.selfloop_edges(G))`，不然 Havel–Hakimi 的對拍會莫名其妙對不上。
2. **以為度數序列相同就同構**：上面的 `T1`、`T2` 就是反例，而且 7 點時有 874 個同構類和別人共用度數序列。不變量只能證「不同構」。
3. **Havel–Hakimi 沒有每輪重新排序**：只在一開始排一次，減 1 之後順序可能被打亂，下一輪取到的就不是最大度數。證明裡的「前 `d1` 名」依賴的是**當前**序列的排序，所以每輪都要排。
4. **二分圖判定只從一個起點 BFS**：不連通的圖，其他分量完全沒被檢查。上面的程式外層 `for s in sorted(adj)` 就是為了這個。
5. **把「誘導子圖」和「子圖」混用**：`C4` 是 `K4` 的子圖，但**不是** `K4` 的誘導子圖（`K4` 任取 4 點誘導出來的是 `K4` 本身）。Ch 16 的 chordal 定義用的是誘導，搞混會讓你以為 `K4` 不是 chordal。

## 進階：再往深一層

- **2-交換的連通性**：Havel–Hakimi 證明裡的 2-交換還有更強的結論——同一個度數序列的任兩個實現，都可以靠一連串 2-交換互相轉換（West 在 Ch 1 講度數序列時就有這個定理；它的證明和本章 Havel–Hakimi 的交換論證幾乎一樣，可以先自己試）。這是「隨機產生指定度數序列的圖」的 MCMC 方法（`nx.double_edge_swap`）的理論基礎，也是網路科學裡做零模型（null model）的標準工具。
- **有向圖與二分圖版本**：二分圖的兩側度數序列是否可實現有 Gale–Ryser 定理，有向圖的（出度, 入度）序列對也有形式和 Erdős–Gallai 很像的條件；NetworkX 的 `is_digraphical`、`is_multigraphical` 等函式就實作了這幾類判定。
- **Certifying algorithm**：McConnell、Mehlhorn、Näher、Schweitzer 的綜述〈Certifying algorithms〉（Computer Science Review 5(2)，2011）把「交出可驗證證據」系統化。二分圖判定是最小的例子；Ch 18 的平面性測試會交出 Kuratowski 子圖當證據。
- **同構在資安的用途**：binary 相似度比對（Ch 40）在函式 CFG 上做近似同構與圖特徵；惡意程式家族分群會用 WL 類的 hash。它們都不解精確 GI，而是承認「不變量只能單向」然後設計夠用的特徵。

## 動手練習

1. 把 `havel_hakimi` 改成每輪**不重新排序**，在 `n ≤ 6` 的窮舉上找出第一個判錯的序列。它把可圖判成不可圖，還是反過來？
2. 寫 `erdos_gallai_fast`：排序一次後用前綴和讓每個 `k` 的檢查變成 `O(1)` 均攤，並和原版在 `n ≤ 6` 上對拍。
3. 改 `bipartition_or_odd_cycle` 為 DFS 版本，確認回傳的仍然是**奇**圈（DFS 樹上的非樹邊不一定連接同深度的點，想想為什麼仍然成立）。
4. 從 `nx.graph_atlas_g()` 找出 `n = 6` 中 WL hash 撞桶的 8 個類別，畫出其中一對，說說它們差在哪。

## 本章重點整理

- 本課預設簡單圖；多重圖、有向圖、加權圖要明說。路徑不重複點，跡不重複邊。
- 握手引理 `Σ deg = 2|E|` 是雙重計數；推論是奇度點有偶數個。
- Havel–Hakimi 用極端原理 + 2-交換證明「最大度數點可以連到度數最大的那群點」，從而得到構造演算法；Erdős–Gallai 給出等價的不等式組（本章證必要性，充分性只給架構）。
- 二分圖 ⇔ 無奇圈。證明是構造性的：BFS 衝突邊 + 最近共同祖先就是一個奇圈。
- 不變量只能證明「不同構」。同構問題的複雜度至今未定，Babai 給出 quasi-polynomial 演算法。

## 自我檢核

- [ ] 不看筆記，能重述 Havel–Hakimi（⇒）方向的證明：選什麼極端的實現、2-交換換掉哪四條邊、為什麼新圖仍是簡單圖。
- [ ] 能解釋 Erdős–Gallai 不等式右邊兩項各自在數什麼。
- [ ] 能在白板上證明「無奇圈 ⇒ 二分圖」，並指出證明裡哪一步用到了「最近」共同祖先。
- [ ] 如果有人說「我比對了兩張 CFG 的節點數、邊數、度數分佈都一樣，所以兩個函式結構相同」，你能用本章的反例說明錯在哪。
- [ ] 能說出鄰接矩陣在什麼情境下比鄰接字典好，以及反過來。

## 延伸閱讀

### 論文

- **[On Realizability of a Set of Integers as Degrees of the Vertices of a Linear Graph. I](https://doi.org/10.1137/0110037)** — S. L. Hakimi，J. Soc. Indust. Appl. Math. 10(3):496–506（1962）
  - **核心貢獻**：獨立於 Havel 提出本章的遞迴判準，並討論多重圖的版本。Havel 的原作是捷克文的 [A remark on the existence of finite graphs](https://doi.org/10.21136/CPM.1955.108220)（Časopis pro pěstování matematiky 80(4):477–480，1955），所以這個定理掛兩個人的名字。
  - **讀哪裡**：前兩節的定理敘述與證明；把它和本章的 2-交換證明對照。
  - **前提**：本章的 Havel–Hakimi 一節。
- **[A simple proof of the Erdős–Gallai theorem on graph sequences](https://doi.org/10.1017/S0004972700002872)** — S. A. Choudum，Bull. Austral. Math. Soc. 33(1):67–70（1986）
  - **核心貢獻**：用歸納法在四頁內證完本章沒證的充分性方向。Erdős 與 Gallai 的原論文是匈牙利文（Matematikai Lapok 11，1960），Rényi 研究所的 Erdős 論文庫有[掃描檔](https://www.renyi.hu/~p_erdos/1961-05.pdf)。
  - **讀哪裡**：整篇；關鍵在它怎麼挑「要減 1 的那個點」讓不等式組保持成立。
  - **前提**：本章 EG 必要性證明；熟悉歸納法（Ch 2）。
- **[Graph Isomorphism in Quasipolynomial Time](https://arxiv.org/abs/1512.03547)** — László Babai，STOC 2016（[會議版 DOI](https://doi.org/10.1145/2897518.2897542)）
  - **核心貢獻**：把 GI 從 `exp(O(√(n log n)))` 推進到 quasi-polynomial，是近幾十年複雜度理論最受矚目的結果之一。
  - **讀哪裡**：arXiv 版第 1 節（引言與歷史）就夠了，後面 80 頁是群論，不是本課範圍。
  - **前提**：知道 P、NP 的定義（Ch 34 會補）。

### 書籍

- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **這本書的定位**：本課主教科書。書雖然超過 20 年，但這一章的內容是穩定的古典結果，不受影響。
  - **讀哪幾章**：Ch 1「Fundamental Concepts」涵蓋本章全部主題（定義、同構、二分圖、度數序列），習題很多，挑有星號以外的做。
  - **前提**：無。
- **《Graph Theory》** — Reinhard Diestel（Springer GTM 173，第 6 版，2025）
  - **這本書的定位**：比 West 精煉、更偏數學系寫法；官網 diestel-graph-theory.com 提供正文的免費線上預覽。
  - **讀哪幾章**：Ch 1「The Basics」——術語定義的「標準答案」，本課用詞和它一致。
  - **前提**：讀過本章，習慣「定義 → 引理 → 證明」的節奏。

### 官方文件

- **[NetworkX `is_graphical`](https://networkx.org/documentation/stable/reference/generated/networkx.algorithms.graphical.is_graphical.html)**
  - **讀哪裡**：`method` 參數——`'eg'`（Erdős–Gallai，預設）與 `'hh'`（Havel–Hakimi）。同一模組還有有向圖、多重圖版本的判定函式。
  - **注意**：它的 EG 實作用的是 Zverovich–Zverovich 的改寫形式（NetworkX 3.7 原始碼 `algorithms/graphical.py` 的註解有寫），檢查次數比本章逐個 `k` 直譯少，值得對照讀。

度數序列和二著色都是「局部資訊能不能拼成全域結構」的問題。下一章把同樣的問題問在路線上：每條邊恰走一次很容易判斷，每個點恰走一次卻困難到成為 NP-complete 的代表。

→ [Ch 8 Euler 與 Hamilton：容易與困難的分水嶺](./08-euler-hamilton.md)
