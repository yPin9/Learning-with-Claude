# Ch 16 — Chordal graph 與完美消去序

> **目標**：證明「chordal ⟺ 有完美消去序」、證明 chordal graph 上反向完美消去序的貪婪著色恰好用 `ω = χ` 色，自刻 Maximum Cardinality Search 與 LexBFS 並在 n ≤ 7 的全部圖上對拍；最後證明 strict SSA 程式的 interference graph 是 chordal，並親手看到「非 SSA 需要 3 個暫存器、SSA 只要 2 個但多了一個 swap」。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

Ch 15 的結論很悲觀：著色是 NP-hard，`χ` 和最大團 `ω` 可以差任意多，貪婪法的品質全看運氣。可是編譯器每天在幾萬個函式上做暫存器分配，總不能每次都解 NP-hard 問題。

出路是**限制圖的形狀**。如果輸入的圖屬於某個特殊類別，NP-hard 問題常常會變成多項式時間。chordal graph（弦圖）是這種「好結構」最重要的例子之一，而且它不是數學家硬造出來的：

- **稀疏矩陣分解**：對稱矩陣做 Gaussian elimination / Cholesky 時，消去一個變數會在它的鄰居之間補上新的非零元（fill-in）。Rose（1970）、Rose–Tarjan–Lueker（1976）發現「零 fill-in 的消去順序存在」恰好等於「圖是 chordal」——**完美消去序**這個名字就是從「消去」來的。
- **暫存器分配**：2005 年前後，好幾個團隊各自發現 SSA 形式程式的 interference graph 一定是 chordal（Hack、Grund、Goos；Bouchez、Darte、Rastello；Brisk 等人；Pereira、Palsberg）。這代表 SSA 上的著色可以**多項式時間做到最佳**，而且需要的暫存器數恰好等於「同時活著的變數最大數量」。
- **Treewidth**（Ch 36）：圖的 treewidth 定義成「把它補成 chordal 後最大團 −1 的最小值」。chordal graph 是樹分解的骨架。

## 先建立直覺

**chordal graph**：每個長度 `≥ 4` 的圈都有**弦**（chord，連接圈上兩個不相鄰點的邊）。等價說法：沒有長度 `≥ 4` 的**導出圈**（induced cycle）。

```text
  不是 chordal              是 chordal（加了弦 a-c）
   a ─── b                   a ─── b
   │     │                   │ ╲   │
   │     │                   │   ╲ │
   d ─── c                   d ─── c
  C4 沒有弦                 每個長圈都被切成三角形
```

直覺是「全部由三角形黏起來，而且黏的方式像一棵樹」。想像從一個 chordal graph 不斷拔掉點：每次總找得到一個點，它的鄰居兩兩相鄰（鄰居是一個團）。這種點叫 **simplicial vertex（單純點）**。拔掉它不會破壞任何東西——它的鄰居本來就全連在一起，消去時不需要補邊（零 fill-in）。

```text
拔點順序（完美消去序，PEO）：
  v1 是 G 的單純點
  v2 是 G - v1 的單純點
  v3 是 G - v1 - v2 的單純點
  ...
等價說法：每個 v_i 「排在它後面的鄰居」兩兩相鄰
```

**完美消去序（perfect elimination ordering，PEO）**：一個點的排列 `v1, ..., vn`，使每個 `v_i` 在 `{v_{i+1}, ..., v_n}` 裡的鄰居構成團。

> 團（clique）、導出子圖的定義在 [Ch 7 — 圖的定義、表示與同構](./07-graph-definitions.md)；色數、`ω`、貪婪著色在 [Ch 15 — 著色：貪婪、Brooks、Vizing](./15-graph-coloring.md)。

## 核心定理：chordal ⟺ 有 PEO

要證明「chordal 一定有單純點」，需要一個關於分隔集的引理。

**定義**：a、b 不相鄰，點集 S 叫 **a-b 分隔集**，如果 `G - S` 裡 a、b 不連通；**極小**是指 S 的任何真子集都不是 a-b 分隔集。

**引理 1（Dirac 1961）**：chordal graph 的每個極小分隔集都是團。

**證明**：設 S 是極小 a-b 分隔集，A、B 分別是 `G - S` 中含 a、含 b 的連通分量。因為 S 極小，S 的每個點 s 在 A 和 B 裡都有鄰居（否則把 s 拿出 S，a、b 仍被分開）。取 S 中任兩點 x、y，要證明它們相鄰：

- 取一條 x 到 y、中間點全在 A 裡的**最短**路徑 P（存在，因為 x、y 都有 A 中的鄰居、A 連通）。
- 同樣取一條中間點全在 B 裡的最短路徑 Q。
- P 和 Q 拼成一個長度 `≥ 4` 的圈（P、Q 各至少 2 條邊）。G 是 chordal，這個圈有弦。
- 弦不能連 A 的點和 B 的點（A、B 在 `G - S` 裡是不同分量）；不能連 P 內部兩個不相鄰的點（P 是最短路徑，有這條邊 P 就能更短），Q 同理；也不能從 x 連到 P 的非相鄰內點（同樣會讓 P 變短），y、Q 同理。剩下唯一可能的弦是 `x-y`。∎

**引理 2（Dirac）**：每個 chordal graph 都有單純點；若它不是完全圖，則有兩個**不相鄰**的單純點。

**證明**：對點數歸納。完全圖每個點都是單純點。設 G 不是完全圖，取不相鄰的 a、b，取極小 a-b 分隔集 S（由引理 1 是團），A、B 如前。看導出子圖 `H = G[A ∪ S]`，它是 chordal（chordal 的導出子圖還是 chordal——導出子圖的導出圈也是原圖的導出圈），而且點數比 G 少（B 不在裡面）。

- 若 H 是完全圖，A 中任一點都是 H 的單純點。
- 否則由歸納假設，H 有兩個不相鄰的單純點；S 是團，它們不可能都在 S，所以至少一個在 A。

不管哪種，A 中有一點 `s_A` 是 H 的單純點。關鍵：`s_A` 在 G 中的鄰居全在 `A ∪ S` 裡（A 是 `G - S` 的分量，A 的點碰不到 B 或其他分量），所以它在 G 中的鄰居集合等於在 H 中的鄰居集合，是團——`s_A` 也是 G 的單純點。同理 B 中有 `s_B`。`s_A`、`s_B` 分屬不同分量，不相鄰。∎

**定理 3（Fulkerson–Gross 1965）**：G 是 chordal ⟺ G 有完美消去序。

**證明**：

- （⇒）由引理 2 取單純點 `v1`，`G - v1` 仍是 chordal，再取它的單純點 `v2`，依此類推。每個 `v_i` 在剩下的圖中的鄰居構成團，這正是 PEO 的定義。
- （⇐）假設 G 有 PEO 但有一個長度 `≥ 4` 的導出圈 C。取 C 上在 PEO 中**最早**的點 v，它在 C 上的兩個鄰居都排在 v 後面，由 PEO 定義它們相鄰——這是 C 的弦，矛盾。∎

（⇐）方向的證明值得記住它的形狀：「取最早的那個，它後面的鄰居必須相鄰」。這個論證在本章還會用兩次。

## 演算法：怎麼找 PEO

照定理 3 的（⇒）方向，「反覆找單純點」可以構造 PEO，但每次檢查單純點要 `O(deg²)`，整體很慢。兩個線性時間的搜尋法直接產生 PEO 的**反序**：

```text
Maximum Cardinality Search（MCS，Tarjan–Yannakakis 1984）：
  每一步挑「已訪問鄰居最多」的未訪問點。

LexBFS（Rose–Tarjan–Lueker 1976）：
  每個點帶一個標籤（訪問過的鄰居的訪問時間序列），挑字典序最大的。
  實作上用「分割細化」：維護一串點集合，每訪問一個點 v，
  把每個集合切成「v 的鄰居」+「非鄰居」兩半，鄰居那半排前面。

兩者都滿足：G 是 chordal ⟺ 搜尋順序倒過來是 PEO
```

所以 chordal 判定 = 跑一次 MCS 或 LexBFS + 檢查結果是不是 PEO。檢查也有線性時間的技巧（Rose–Tarjan–Lueker）：

> 對每個點 v，令 `L(v)` 為它在 PEO 中排在後面的鄰居，`u` 是 `L(v)` 裡最早的那個。只檢查「`L(v) - {u}` 全是 u 的鄰居」。

為什麼只檢查一個 u 就夠？**證明**：假設每個 v 都通過檢查，但有某些 `L(v)` 不是團；取其中排得**最後**的 v。它的 u 排在 v 後面，所以 `L(u)` 是團。`L(v) - {u}` 的點都和 u 相鄰、又都排在 u 後面，所以 `L(v) - {u} ⊆ L(u)`，是團；再加上 u 和它們全部相鄰，`L(v)` 是團，矛盾。∎

**MCS 和 LexBFS 的正確性這裡只給證明架構**：兩者的證明都是對搜尋順序做歸納，證明「若 G 是 chordal，每個新訪問的點，其已訪問鄰居構成團」，關鍵工具是「已訪問點中，任兩個不相鄰的點若都有通往未訪問點的路徑，會產生無弦長圈」。完整論證見延伸閱讀的 Tarjan–Yannakakis 論文第 2 節。我們用窮舉代替：在 1252 張小圖上，同時比對「定義（暴力找無弦圈）」「MCS」「LexBFS」「NetworkX」四方答案。

```python
from itertools import combinations
import networkx as nx


def mcs(adj):
    """Maximum Cardinality Search；平手時挑名字最小的點，結果可重現。"""
    rank = {v: i for i, v in enumerate(sorted(adj, key=str))}
    weight = {v: 0 for v in adj}
    order, visited = [], set()
    for _ in range(len(adj)):
        v = max((u for u in adj if u not in visited), key=lambda u: (weight[u], -rank[u]))
        order.append(v)
        visited.add(v)
        for w in adj[v]:
            if w not in visited:
                weight[w] += 1
    return order


def lexbfs(adj):
    """分割細化版 LexBFS。"""
    parts = [sorted(adj, key=str)]
    order = []
    while parts:
        v = parts[0].pop(0)
        if not parts[0]:
            parts.pop(0)
        order.append(v)
        new = []
        for p in parts:
            inside = [u for u in p if u in adj[v]]
            outside = [u for u in p if u not in adj[v]]
            new += [q for q in (inside, outside) if q]
        parts = new
    return order


def peo_violation(adj, peo):
    """合法 PEO 回傳 None；否則回傳證據 (v, u, 缺邊的點)。"""
    pos = {v: i for i, v in enumerate(peo)}
    for v in peo:
        later = [w for w in adj[v] if pos[w] > pos[v]]
        if later:
            u = min(later, key=pos.get)
            missing = [w for w in later if w != u and w not in adj[u]]
            if missing:
                return v, u, missing
    return None


def is_chordal(adj):
    return peo_violation(adj, mcs(adj)[::-1]) is None


def chordal_by_definition(adj):
    """暴力：沒有 >= 4 點的導出子圖是圈。"""
    for k in range(4, len(adj) + 1):
        for S in combinations(adj, k):
            s = set(S)
            if all(len(adj[v] & s) == 2 for v in S):
                if nx.is_connected(nx.Graph([(u, w) for u in S for w in adj[u] & s])):
                    return False
    return True


count = {True: 0, False: 0}
for G in nx.graph_atlas_g()[1:]:
    a = {u: set(G[u]) for u in G}
    truth = chordal_by_definition(a)
    assert truth == is_chordal(a) == (peo_violation(a, lexbfs(a)[::-1]) is None) == nx.is_chordal(G)
    count[truth] += 1
print("1252 張圖：chordal", count[True], "張，非 chordal", count[False], "張，四方答案全部一致")

C4 = {0: {1, 3}, 1: {0, 2}, 2: {1, 3}, 3: {0, 2}}
print("C4 的 MCS 反序:", mcs(C4)[::-1], "證據:", peo_violation(C4, mcs(C4)[::-1]))
# chordal 圖上「隨便一個順序」不一定是 PEO：一條路徑 0-1-2，中間點先拔
P3 = {0: {1}, 1: {0, 2}, 2: {1}}
print("P3 順序 [1,0,2]:", peo_violation(P3, [1, 0, 2]), "  順序 [0,1,2]:", peo_violation(P3, [0, 1, 2]))
```

```text
1252 張圖：chordal 531 張，非 chordal 721 張，四方答案全部一致
C4 的 MCS 反序: [3, 2, 1, 0] 證據: (3, 2, [0])
P3 順序 [1,0,2]: (1, 0, [2])   順序 [0,1,2]: None
```

C4 的證據讀法：點 v 排在前面，它後面的鄰居裡最早的是 u，但 u 和「缺邊的點」不相鄰——v 的後面鄰居不是團。這就是**不是 chordal 的可驗證證據**，比回傳一個 `False` 有用得多。路徑 P3 的例子說明 chordal graph 也不是任何順序都行：先拔中間點，它的兩個鄰居不相鄰。

## 底層機制：為什麼 PEO 讓著色變成最佳

**定理 4**：G 是 chordal、`v1..vn` 是 PEO。依 `vn, ..., v1`（PEO 的反序）做貪婪著色，用色數恰好是 `ω(G)`，因此 `χ(G) = ω(G)`。

**證明**：輪到 `v_i` 時，已著色的點是 `v_{i+1}..v_n`，所以已著色的**鄰居**正是 `L(v_i)`。由 PEO，`L(v_i)` 是團，`{v_i} ∪ L(v_i)` 也是團，大小 `≤ ω`，所以 `|L(v_i)| ≤ ω - 1`，它們最多擋掉 `ω - 1` 個顏色，`v_i` 拿到的顏色編號 `≤ ω - 1`。總用色 `≤ ω ≤ χ`，而任何著色都 `≥ χ`，所以全部相等。∎

同一個結構還順便給出**最大團**：

**推論**：`ω(G) = max_i (1 + |L(v_i)|)`。任何團 K，取 K 中在 PEO 裡最早的點 v，`K - {v}` 全在 `L(v)` 裡，所以 `|K| ≤ 1 + |L(v)|`；反過來每個 `{v} ∪ L(v)` 都是團。

```text
       PEO:  v1   v2   v3   ...   vn
                                   ◀── 著色方向（從後往前）
  輪到 v_i：已著色的鄰居 = L(v_i) = 一個團
            → 它們顏色兩兩不同，共 |L(v_i)| 個
            → v_i 只被擋掉 |L(v_i)| ≤ ω-1 個顏色
```

對比 Ch 15：一般圖上貪婪法每個點最多被擋 `Δ` 個顏色；chordal 加 PEO 把這個數字換成「團大小 −1」。最大團、最大獨立集、最小團覆蓋、色數這四個在一般圖上都 NP-hard 的問題，在 chordal graph 上都是線性時間（Gavril 1972）。

```python
# 接續上一個 block 的定義
def color_by_peo(adj):
    peo = mcs(adj)[::-1]
    color = {}
    for v in reversed(peo):                         # PEO 反序 = MCS 原順序
        used = {color[u] for u in adj[v] if u in color}
        color[v] = next(c for c in range(len(adj)) if c not in used)
    pos = {v: i for i, v in enumerate(peo)}
    omega = max(1 + sum(pos[w] > pos[v] for w in adj[v]) for v in adj)
    return color, omega


def chromatic_number(adj):
    nodes = sorted(adj, key=lambda v: -len(adj[v]))
    def can(k):
        color = {}
        def bt(i):
            if i == len(nodes):
                return True
            v = nodes[i]
            used = {color[u] for u in adj[v] if u in color}
            for c in range(min(k, max(color.values(), default=-1) + 2)):
                if c not in used:
                    color[v] = c
                    if bt(i + 1):
                        return True
                    del color[v]
            return False
        return bt(0)
    k = 1
    while not can(k):
        k += 1
    return k


natural_bad = 0
checked = 0
for G in nx.graph_atlas_g()[1:]:
    a = {u: set(G[u]) for u in G}
    if not is_chordal(a):
        continue
    color, omega = color_by_peo(a)
    assert all(color[u] != color[w] for u in a for w in a[u])
    k = len(set(color.values()))
    nx_omega = max(len(c) for c in nx.find_cliques(G))
    assert k == omega == nx_omega == chromatic_number(a)
    nat = {}
    for v in sorted(a):                             # 對照：自然順序貪婪
        used = {nat[u] for u in a[v] if u in nat}
        nat[v] = next(c for c in range(len(a)) if c not in used)
    natural_bad += len(set(nat.values())) > k
    checked += 1
print(f"{checked} 張 chordal 圖：PEO 著色色數 = ω = nx 最大團 = 暴力 χ，全部成立")
print(f"同樣這些圖，自然順序貪婪多用顏色的有 {natural_bad} 張")
```

```text
531 張 chordal 圖：PEO 著色色數 = ω = nx 最大團 = 暴力 χ，全部成立
同樣這些圖，自然順序貪婪多用顏色的有 69 張
```

就算圖是 chordal，順序不對貪婪法照樣會多用顏色；結構要配上對的順序才有用。

## 子樹交集圖：chordal 的另一張臉

chordal 還有一個和暫存器分配直接相關的刻畫：

**定理（Gavril 1974；Buneman、Walter 各自獨立）**：G 是 chordal ⟺ G 是某棵樹 T 的一族子樹的**交集圖**（每個點對應 T 的一棵連通子樹，兩點相鄰 ⟺ 兩棵子樹有共同節點）。

我們證明會用到的方向（⇐）：**子樹的交集圖一定是 chordal**。

**證明**：對樹 T 的節點數歸納，證明交集圖 G 有單純點（再對拿掉該點後的子樹族重複，得到 PEO）。取 T 的一個葉子 ℓ，令 p 為它的父親。

- 若某棵子樹恰好只含 ℓ 這一個節點——或更一般地，某棵子樹 `S_v` 含 ℓ 但不含 p（那 `S_v = {ℓ}`，因為子樹連通、ℓ 只連 p）——那所有和 `S_v` 相交的子樹都含 ℓ，它們兩兩在 ℓ 相交，v 的鄰居是團，v 是單純點。
- 否則所有含 ℓ 的子樹也都含 p。把 ℓ 從 T 和所有子樹中刪掉：每棵子樹仍連通、非空，而且任兩棵子樹的相交關係不變（在 ℓ 相交的兩棵也都在 p 相交）。交集圖不變，T 少了一個節點，由歸納假設得到單純點。

基底是 T 只有一個節點：所有子樹都是那個節點，交集圖是完全圖，每點都是單純點。∎

**區間圖**（interval graph，數線上區間的交集圖）是樹退化成路徑的特例，所以也是 chordal。這就是 linear scan 暫存器分配的模型：直線程式裡變數的活躍範圍是區間（Ch 17 會再碰到）。

```python
# 接續上一個 block 的定義
import random

random.seed(16)
trials = 0
for _ in range(2000):
    n = random.randint(2, 12)
    T = nx.random_labeled_tree(n, seed=random.randrange(10**9))
    subtrees = []
    for _ in range(random.randint(2, 10)):
        nodes = {random.randrange(n)}                    # 從一點出發，隨機長成連通子樹
        for _ in range(random.randint(0, n)):
            frontier = [w for u in nodes for w in T[u] if w not in nodes]
            if frontier:
                nodes.add(random.choice(frontier))
        subtrees.append(nodes)
    a = {i: {j for j in range(len(subtrees)) if j != i and subtrees[i] & subtrees[j]}
         for i in range(len(subtrees))}
    assert is_chordal(a)
    trials += 1
print(f"{trials} 個隨機樹的隨機子樹族：交集圖全部是 chordal")
```

```text
2000 個隨機樹的隨機子樹族：交集圖全部是 chordal
```

## SSA 的 interference graph 是 chordal

**interference graph**：每個變數一個點；兩個變數在某個程式點上**同時活著**就相鄰（活躍性 liveness 的計算在 Ch 17 與 [ssa_optimizations Ch 8 — Liveness](../../compilers/ssa_optimizations/08-liveness-analysis.md)）。**strict SSA**：每個變數只被定義一次，且每個使用點都被定義點支配（dominate，Ch 14）。

**定理（Hack、Grund、Goos 2006；Bouchez–Darte–Rastello、Brisk 等人、Pereira–Palsberg 在 2005 年前後各自得到）**：strict SSA 程式的 interference graph 是 chordal。

我們給一個不經過 Gavril 定理、直接構造 PEO 的證明。約定：變數 x 在程式點 p 活著，指存在一條從 p 到 x 某個使用點、且不經過 `def(x)` 的路徑；兩個變數干擾，指存在一個程式點兩者都活著（定義點之後緊接的那一點，視被定義的變數為活著）。

**引理 A**：在 strict SSA 中，x 在 p 活著 ⟹ `def(x)` 支配 p。

**證明**：若不支配，存在 entry 到 p 不經過 `def(x)` 的路徑；接上 p 到 x 使用點、不經過 `def(x)` 的路徑，得到 entry 到使用點、完全不經過 `def(x)` 的路徑，違反 strict 的定義。∎

**引理 B**：x、y 干擾，且 `def(x)` 在支配樹的前序（preorder）中排在 `def(y)` 前面，則 x 在 `def(y)` 之後那一點活著。

**證明**：設 p 是兩者同時活著的點。由引理 A，`def(x)` 和 `def(y)` 都支配 p。一個點的所有支配者在支配樹上排成一條鏈（都是 p 的祖先），所以兩者有祖先關係；前序中 `def(x)` 在前，所以 `def(x)` 支配 `def(y)`，且兩者不同點。

接著找一條從 `def(y)` 到 p、不經過 `def(x)` 的路徑：取任一條 entry 到 p 的路徑，它必經過 `def(y)`；取 `def(y)` **最後一次**出現之後的那一段 R。若 R 經過 `def(x)`（設在點 q），取一條 entry 到 `def(x)` 且不經過 `def(y)` 的路徑（存在，因為 `def(y)` 不支配 `def(x)`），接上 R 從 q 到 p 的部分（不含 `def(y)`，因為 R 是最後一次之後），得到 entry 到 p 不經過 `def(y)` 的路徑，和「`def(y)` 支配 p」矛盾。所以 R 不經過 `def(x)`。R 接上「p 到 x 使用點、不經過 `def(x)`」的路徑，證明 x 在 `def(y)` 之後活著。∎

**定理證明**：把變數依定義點在支配樹前序中的順序排列。對任何變數 y，看它排在前面的鄰居 x（和 y 干擾、定義較早）：由引理 B，它們**全部**在 `def(y)` 之後那一點同時活著，所以兩兩干擾，構成團。也就是說，這個順序裡「每個點前面的鄰居是團」——它的**反序**是 PEO。由定理 3，interference graph 是 chordal。∎

而且這個證明直接給出演算法：**照定義點的支配樹前序做貪婪著色**，就是 PEO 反序的著色，由定理 4 用色數 `= ω`。在 SSA 上，`ω` 等於某個程式點同時活著的變數數的最大值（maxlive）：團的所有成員都在團中最晚定義者的定義點之後同時活著。所以**暫存器需求 = maxlive，不多不少**。

### 實際看一次：非 SSA 要 3 個，SSA 只要 2 個

下面這個迴圈讓 5 個變數的活躍範圍在迴圈上「繞一圈」，形成 `C5`：

```text
非 SSA                         SSA
  a = 0                          a0 = 0
  b = 1                          b0 = 1
L:                             L: a1 = φ(a0, a5); b1 = φ(b0, b5)
  c = a + 1    live {b,c}        c1 = a1 + 1
  d = b + 1    live {c,d}        d1 = b1 + 1
  e = c + 1    live {d,e}        e1 = c1 + 1
  a = d + 1    live {e,a}        a5 = d1 + 1
  b = e + 1    live {a,b}        b5 = e1 + 1
  if b < 100 goto L              if b5 < 100 goto L
  print a, b                     print a5, b5
```

每個程式點最多只有 2 個變數活著，可是非 SSA 版的 interference graph 是 5-圈，`χ(C5) = 3`。我們把程式寫成「指令 = (標籤, 定義, 使用, 後繼)」，φ 的語意照標準做法拆開：φ 的運算元在前驅區塊的**尾端**被讀（`j`、`back` 兩條虛擬指令），φ 的結果在迴圈頭**平行**定義。

```python
# 接續上一個 block 的定義
NON_SSA = [
    ("e1", {"a"}, set(), ["e2"]),   ("e2", {"b"}, set(), ["L"]),
    ("L", {"c"}, {"a"}, ["i2"]),    ("i2", {"d"}, {"b"}, ["i3"]),
    ("i3", {"e"}, {"c"}, ["i4"]),   ("i4", {"a"}, {"d"}, ["i5"]),
    ("i5", {"b"}, {"e"}, ["br"]),   ("br", set(), {"b"}, ["L", "x"]),
    ("x", set(), {"a", "b"}, []),
]
SSA = [
    ("e1", {"a0"}, set(), ["e2"]),  ("e2", {"b0"}, set(), ["j"]),
    ("j", set(), {"a0", "b0"}, ["L"]),                  # φ 在入口邊上讀 a0、b0
    ("L", {"a1", "b1"}, set(), ["i1"]),                 # φ 平行定義 a1、b1
    ("i1", {"c1"}, {"a1"}, ["i2"]), ("i2", {"d1"}, {"b1"}, ["i3"]),
    ("i3", {"e1"}, {"c1"}, ["i4"]), ("i4", {"a5"}, {"d1"}, ["i5"]),
    ("i5", {"b5"}, {"e1"}, ["br"]), ("br", set(), {"b5"}, ["back", "x"]),
    ("back", set(), {"a5", "b5"}, ["L"]),               # φ 在回邊上讀 a5、b5
    ("x", set(), {"a5", "b5"}, []),
]


def live_out_sets(prog):
    defs = {l: d for l, d, u, s in prog}
    uses = {l: u for l, d, u, s in prog}
    succ = {l: s for l, d, u, s in prog}
    lin = {l: set() for l in defs}
    lout = {l: set() for l in defs}
    changed = True
    while changed:                                      # 反向資料流，跑到不動點
        changed = False
        for l in reversed(list(defs)):
            out = set().union(*(lin[s] for s in succ[l]))
            inn = uses[l] | (out - defs[l])
            if (out, inn) != (lout[l], lin[l]):
                lout[l], lin[l], changed = out, inn, True
    return lout


def interference(prog):
    lout = live_out_sets(prog)
    adj = {}
    for l, d, u, s in prog:
        for x in d | u:
            adj.setdefault(x, set())
        for x in d:
            for y in lout[l] | d:
                if x != y:
                    adj[x].add(y)
                    adj[y].add(x)
    return adj, max(len(o) for o in lout.values())


for name, prog in [("非 SSA", NON_SSA), ("SSA", SSA)]:
    a, maxlive = interference(prog)
    G = nx.Graph([(u, w) for u in a for w in a[u]])
    holes = [sorted(c) for c in nx.chordless_cycles(G) if len(c) >= 4]
    print(f"{name}: maxlive={maxlive} chordal={is_chordal(a)} χ={chromatic_number(a)} 無弦長圈={holes}")

# SSA：照定義順序（這段程式沒有分支進迴圈體，定義順序就是支配樹前序）貪婪著色
a, _ = interference(SSA)
def_order = [x for l, d, u, s in SSA for x in sorted(d)]
reg = {}
for v in def_order:
    used = {reg[u] for u in a[v] if u in reg}
    reg[v] = next(c for c in range(9) if c not in used)
print("SSA 暫存器:", {v: f"R{reg[v]}" for v in def_order})
print("回邊上 φ 需要的搬移:", [f"R{reg[dst]} ← R{reg[src]}" for dst, src in [("a1", "a5"), ("b1", "b5")]])
```

```text
非 SSA: maxlive=2 chordal=False χ=3 無弦長圈=[['a', 'b', 'c', 'd', 'e']]
SSA: maxlive=2 chordal=True χ=2 無弦長圈=[]
SSA 暫存器: {'a0': 'R0', 'b0': 'R1', 'a1': 'R0', 'b1': 'R1', 'c1': 'R0', 'd1': 'R1', 'e1': 'R0', 'a5': 'R1', 'b5': 'R0'}
回邊上 φ 需要的搬移: ['R0 ← R1', 'R1 ← R0']
```

輸出把整件事攤開了：

- 非 SSA 的 interference graph 有一個 5-圈 `a-b-c-d-e`，maxlive 只有 2，卻需要 3 個暫存器。這是 Ch 15「`χ` 可以大於 `ω`」在真實程式裡的樣子。
- SSA 版變成一條路徑（chordal），`χ = maxlive = 2`。
- **代價**：回邊上的 φ 要求 `R0 ← R1` 和 `R1 ← R0` **同時**發生——一個 swap。φ 的語意是平行複製，消除 SSA 時要把平行複製排成循序的 move；遇到循環就需要第三個暫存器、`xchg` 指令或 XOR swap。這就是「SSA 上著色是最佳的」背後的但書：最佳是對**SSA 形式**的程式，SSA 消除（[ssa_optimizations Ch 6 — SSA deconstruction](../../compilers/ssa_optimizations/06-ssa-deconstruction.md)）時的複製還要另外處理。

## 對比與取捨

| 方法 | 時間 | 給你什麼 | 何時用 |
| --- | --- | --- | --- |
| 反覆找單純點 | `O(n · Σ deg²)` 左右 | PEO，直接照定義 | 教學、驗證 |
| MCS + RTL 檢查 | `O(n + m)`（用 bucket；本章 `max` 掃描版是 `O(n²)`） | chordal 判定 + PEO + 不是 chordal 時的證據 | 實作最短 |
| LexBFS + RTL 檢查 | `O(n + m)`（用分割細化的鏈結串列版；本章 list 版較慢） | 同上；LexBFS 順序還有很多額外性質 | 識別區間圖、cograph 等其他圖類 |
| `nx.is_chordal` | 線性 | 只回 True/False | 對答案 |
| 一般圖精確著色 | 指數 | `χ` | 圖不是 chordal 又需要精確解 |

SSA 暫存器分配的設計空間也在這張表的延長線上：

| 做法 | 著色品質 | 代價 |
| --- | --- | --- |
| 傳統（Chaitin–Briggs，先離開 SSA 再著色） | 啟發式，可能多用暫存器 | φ 變成 move，再靠 coalescing 消掉 |
| SSA 上著色（Hack 一系） | 最佳（`= maxlive`） | spill 要先把 maxlive 壓到 `≤ k`；φ 的平行複製可能需要 swap |
| Linear scan（區間模型） | 直線程式上接近最佳 | 有分支時把活躍範圍近似成區間，會損失精度 |

## 踩雷集錦

1. **「chordal = 有很多三角形」**：三角形多不代表沒有長導出圈；中心點連到 C5 每個點的輪子（wheel）滿是三角形，但外圈 C5 仍是導出圈，不是 chordal。定義看的是**導出**圈。
2. **「chordal graph 上貪婪法一定最佳」**：要配 PEO 反序。上面的實驗裡自然順序在 chordal 圖上也會多用顏色。
3. **MCS 的方向搞反**：MCS / LexBFS 產生的是 PEO 的**反序**。拿 MCS 原順序去檢查 PEO，chordal 圖也會被判成「不是」。著色時則是照 MCS 原順序著（等於 PEO 反序）。
4. **「SSA 上著色是最佳，所以 SSA 暫存器分配是多項式時間」**：最佳的是「給定 k 個暫存器夠用時的著色」。要在 k 不夠時決定 spill 什麼、以及做完 coalescing 後保持最少 move，都還是 NP-hard（Bouchez、Darte、Rastello 2007 證明了 coalescing 的多個變體是 NP-complete）。
5. **φ 的活躍性語意**：如果把 φ 的運算元當成在迴圈頭被使用，`a0` 和 `a5` 會被誤算成活到迴圈頭，和 `a1` 干擾，圖就不再是你以為的樣子。標準語意是 φ 在**前驅的尾端**讀運算元。

## 進階：再往深一層

- **Perfect graph**：對每個導出子圖都有 `χ = ω` 的圖叫完美圖，chordal 是其中一類。Strong Perfect Graph Theorem（Chudnovsky、Robertson、Seymour、Thomas 2006）：完美 ⟺ 沒有長度 `≥ 5` 的奇導出圈和它的補圖。Brisk 等人最早證明的就是「SSA interference graph 是完美圖」。
- **Clique tree**：chordal graph 的極大團可以排成一棵樹，讓含某點的極大團構成子樹（Gavril 定理的另一方向）。這就是 Ch 36 樹分解的特例，也是稀疏 Cholesky 的 elimination tree / supernode 結構。
- **Minimum fill-in**：把一般圖加最少邊變成 chordal 是 NP-hard（Yannakakis 1981），但它直接決定稀疏矩陣分解的成本，實務用 AMD、nested dissection 等啟發式。
- **SSA 上的 spill 與 coalescing**：Hack 的博士論文把暫存器分配拆成「spill 到 maxlive ≤ k → 照支配樹著色 → 再做 coalescing」三個獨立階段，是現代 SSA-based allocator（例如 libFirm）的骨架。

## 動手練習

1. 把 C4 加一條弦變成 chordal，看 `peo_violation` 的證據消失；再加一個點連到 C4 的兩個對角點，判斷結果是否 chordal，先手推再跑。
2. 寫 `simplicial_elimination(adj)`：照定理 3 的（⇒）方向，反覆找單純點，和 MCS 的結果比對是否都是 PEO（不必相同）。
3. 把 SSA 範例的回邊改成 `b1 = φ(b0, a5)`、`a1 = φ(a0, b5)`（交換），重新算暫存器與回邊搬移，看 swap 是否消失。
4. 修改 `interference`，讓 φ 的運算元在迴圈頭被「使用」（踩雷 5 的錯誤語意），觀察 interference graph 怎麼變。

## 本章重點整理

- chordal ⟺ 沒有長度 `≥ 4` 的導出圈 ⟺ 有完美消去序（每個點後面的鄰居是團）。
- 關鍵引理：chordal 的極小分隔集是團 → 一定有兩個不相鄰的單純點。
- MCS / LexBFS 產生 PEO 的反序；配上 RTL 的「只檢查最早後鄰居」技巧，判定是線性時間，失敗時給出證據。
- PEO 反序貪婪著色用 `ω` 色，所以 chordal graph 上 `χ = ω`，著色、最大團都是線性時間。
- strict SSA 的 interference graph 是 chordal：照定義點支配樹前序著色就是最佳，暫存器需求等於 maxlive；代價是 SSA 消除時的平行複製（可能要 swap）。

## 自我檢核

- [ ] 不看筆記，證明「有 PEO ⟹ chordal」，並說出這個「取最早的點」論證在本章哪兩個地方又出現。
- [ ] 解釋引理 1 的證明裡，為什麼要取 A、B 中的**最短**路徑；換成任意路徑會壞在哪裡？
- [ ] 如果同事說「我們的 IR 是 SSA，所以 k 個暫存器一定夠只要 maxlive ≤ k」，這句話哪裡對、哪裡漏了？（提示：swap、spill、離開 SSA）
- [ ] 不看程式，手推 C4 上 MCS 的順序，並指出它的反序在哪個點違反 PEO。
- [ ] 說出 chordal 在暫存器分配以外的兩個應用，並解釋「消去」這個名字的來源。

## 延伸閱讀

### 論文

- **[Register Allocation for Programs in SSA-Form](https://doi.org/10.1007/11688839_20)** — S. Hack、D. Grund、G. Goos，Compiler Construction（CC 2006），LNCS 3923
  - **核心貢獻**：證明 SSA interference graph 是 chordal，並據此把 spill、著色、coalescing 拆成獨立階段，著色部分可以最佳。
  - **讀哪裡**：第 2–3 節（SSA 活躍性與 chordality 證明），第 4 節（spill 與 coalescing 的啟發式）可後讀。
  - **和本章的關聯**：本章的引理 A、B 和「依支配樹前序著色」就是這篇的核心論證。
- **[Register Allocation via Coloring of Chordal Graphs](https://link.springer.com/chapter/10.1007/11575467_21)** — F. M. Q. Pereira、J. Palsberg，APLAS 2005，LNCS 3780
  - **這篇說什麼**：從實測出發，發現大量 Java 方法的 interference graph 本來就是 chordal，提出以 chordal 著色為核心、對非 chordal 圖也能工作的分配器。
  - **讀哪裡**：引言的統計和演算法章節。
  - **和本章的關聯**：補上「實務上的圖有多 chordal」這個工程問題。
- **[Simple linear-time algorithms to test chordality of graphs, test acyclicity of hypergraphs, and selectively reduce acyclic hypergraphs](https://collaborate.princeton.edu/en/publications/simple-linear-time-algorithms-to-test-chordality-of-graphs-test-a/)** — R. E. Tarjan、M. Yannakakis，SIAM Journal on Computing 13(3)（1984）
  - **核心貢獻**：提出 Maximum Cardinality Search，給出比 LexBFS 更簡潔的線性時間 chordal 判定。
  - **讀哪裡**：第 2 節（MCS 與它的正確性證明，本章只給架構的部分）；hypergraph 部分可跳過。
- **[Algorithmic Aspects of Vertex Elimination on Graphs](https://epubs.siam.org/doi/10.1137/0205021)** — D. J. Rose、R. E. Tarjan、G. S. Lueker，SIAM Journal on Computing 5(2)（1976）
  - **核心貢獻**：LexBFS、線性時間 PEO 檢查，以及消去序與稀疏矩陣 fill-in 的關係。
  - **讀哪裡**：LexBFS 的定義與「檢查只看最早後鄰居」的引理。
  - **前提**：本章的定理 3。
- **[The intersection graphs of subtrees in trees are exactly the chordal graphs](https://doi.org/10.1016/0095-8956(74)90094-X)** — F. Gavril，Journal of Combinatorial Theory Series B 16（1974）
  - **讀哪裡**：主定理的兩個方向；本章只證了（⇐）。

### 書籍

- **《Algorithmic Graph Theory and Perfect Graphs》** — Martin C. Golumbic（Academic Press 1980；第 2 版 Elsevier 2004）
  - **讀哪幾章**：第 4 章 Triangulated Graphs（本章所有定理的標準出處），第 3 章 Perfect Graphs。
  - **定位**：本章的延伸主教材，書雖舊但內容沒有過時。
- **《SSA-based Compiler Design》** — F. Rastello、F. Bouchez Tichadou 編（Springer 2022）
  - **讀哪幾章**：Register Allocation 一章與 SSA Destruction for Machine Code 一章。
  - **前提**：Ch 14 的支配樹、本章的 SSA chordality。

有了「SSA 圖是 chordal」這個理論底氣，下一章回到真實世界：大多數教科書與不少生產編譯器的暫存器分配，仍然是在**非** SSA 的 interference graph 上跑 Chaitin–Briggs 啟發式。我們要親手實作它。

→ [Ch 17 Register allocation：Chaitin–Briggs](./17-register-allocation.md)
