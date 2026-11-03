# Ch 11 — 連通度：割點、橋與 Tarjan

> **目標**：證明無向圖 DFS 樹「沒有橫向邊」，並由此證明 low-link 判準——什麼時候一個點是割點、一條邊是橋；寫出**迭代版** Tarjan 演算法，一次 DFS 找出割點、橋與雙連通分量（支援平行邊、不怕 Python 遞迴上限），與 NetworkX 對拍；最後證明 Whitney 不等式 `κ ≤ λ ≤ δ`，為 Ch 12 的 Menger 定理鋪路。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

「這張網路圖有沒有單點故障（single point of failure）？」是每個做基礎設施、做網路、做資安的人都問過的問題。換成圖論語言：有沒有一個點（機器、路由器、帳號）或一條邊（線路、信任關係），拿掉之後圖就斷成兩半？前者叫**割點（cut vertex / articulation point）**，後者叫**橋（bridge）**。

最直接的做法是「每個點拿掉試試看，跑一次 BFS 看還連不連通」，`O(n(n + m))`。1972 年 Tarjan 的論文說明了 DFS 的一個結構性質，讓一次 DFS、`O(n + m)` 就能全部找出來——那篇論文同時給了有向圖強連通分量的演算法（Ch 13），是 DFS 從「走訪工具」變成「結構分析工具」的轉捩點。

在攻擊圖分析裡（Ch 39），割點就是 chokepoint：所有通往 Domain Admin 的路徑都經過的那台機器。在分散式系統裡，橋就是一旦斷線就造成 network partition 的那條鏈路。

## 先建立直覺

```text
    0 ─── 1          4
     ╲   ╱          ╱ ╲
       2 ──────── 3 ─── 5 ─── 6
      割點    橋   割點   割點  橋
```

- `2–3` 是橋：拿掉它，左邊三角形和右邊分開。
- `2`、`3`、`5` 是割點：拿掉 `2`，`{0, 1}` 和右邊分開；拿掉 `5`，`6` 被孤立。
- 三角形 `{0,1,2}`、橋 `{2,3}`、三角形 `{3,4,5}`、橋 `{5,6}` 是四個**雙連通分量（block）**：圖裡「沒有割點」的極大子圖。割點正好是 block 之間的接頭。

DFS 的直覺是：從 `3` 往下走進 `{4, 5}` 這個子樹，看子樹裡有沒有「後門」——一條繞回 `3` 的**祖先**的邊。有後門，`3` 就不是這個子樹的唯一出口；沒有，`3` 就是割點。low-link 就是在量「後門最高能通到哪裡」。

> 本章假設你寫過遞迴 DFS，並熟悉 Ch 9 的樹術語（祖先、子樹）。如果不熟，先回看 [Ch 9 — 樹：等價定義、Cayley 公式、Prüfer 編碼](./09-trees-cayley-prufer.md)。

## 定義

設 `G` 是無向圖（本章允許平行邊，不允許自環）。

- **割點**：刪掉 `v`（與它的邊）後，`G` 的連通分量數增加。
- **橋**：刪掉邊 `e` 後，連通分量數增加。
- **雙連通分量（biconnected component, block）**：極大的、沒有割點的連通子圖。單獨一條橋也是一個 block。
- **點連通度** `κ(G)`：最少要刪幾個點才能讓 `G` 不連通或只剩一個點；**邊連通度** `λ(G)`：最少要刪幾條邊才能讓 `G` 不連通。`κ ≥ k` 叫 **k-連通**，`λ ≥ k` 叫 **k-邊連通**。所以「連通、`n ≥ 3`、沒有割點」= 2-連通，「連通、沒有橋」= 2-邊連通。

## 底層機制：無向 DFS 樹的結構

對每個連通分量從某個根做 DFS，記錄每個點的發現時間 `disc(v)`，走過的樹邊構成 DFS 樹。

**引理 1（沒有橫向邊）**：在無向圖的 DFS 中，每條非樹邊都連接一個點與它在 DFS 樹上的祖先。

**證明**：取任一邊 `uv`，不妨設 `disc(u) < disc(v)`。`u` 被發現時 `v` 還沒被發現。DFS 在 `u` 上不會結束，直到 `u` 的所有鄰居都被處理過——包括 `v`。所以 `v` 在 `u` 仍在 DFS 堆疊上時被發現，也就是 `v` 是 `u` 的後代。∎

（有向圖沒有這個性質，會有橫向邊（cross edge），這是 Ch 13 SCC 演算法比較複雜的原因。）

所以非樹邊全是「從後代往祖先」的**回邊（back edge）**。定義

```text
low(v) = min( disc(v),
              disc(w) : 存在回邊 xw，x 在 v 的子樹 T(v) 裡 )
```

遞迴形式：`low(v) = min(disc(v), 從 v 出發的回邊另一端的 disc, v 的每個子節點 c 的 low(c))`。這裡的「回邊」不包括 `v` 走進來的那條樹邊**本身**，但**包括**和它平行的另一條邊——這點在多重圖上決定成敗。

`low(c)` 的意義：`c` 的子樹透過「最多一條回邊」最高能爬到哪個祖先。

### 三個判準與證明

令 `T(c)` 表示以 `c` 為根的 DFS 子樹。關鍵觀察：由引理 1，離開 `T(c)` 的邊只有兩種——`c` 和它父節點之間的樹邊，以及從 `T(c)` 往 `c` 的**真祖先**的回邊。

**定理 A（非根割點）**：設 `v` 不是根。`v` 是割點 ⇔ `v` 有某個子節點 `c` 滿足 `low(c) ≥ disc(v)`。

**證明（⇐）**：`low(c) ≥ disc(v)` 表示 `T(c)` 的回邊只能通到 disc 值 `≥ disc(v)` 的祖先。`c` 的真祖先中，disc 值 `≥ disc(v)` 的只有 `v` 本身（`v` 的祖先都比 `v` 早被發現）。所以離開 `T(c)` 的邊全部落在 `v` 上。刪掉 `v`，`T(c)` 和 `v` 的父節點（存在，因為 `v` 不是根）就不連通了。

**證明（⇒）**：設 `v` 的每個子節點 `c` 都有 `low(c) < disc(v)`，也就是 `T(c)` 有一條回邊通到 `v` 的某個真祖先。刪掉 `v` 後：DFS 樹扣掉 `T(v)` 的部分仍連通（它是一棵包含根的子樹）；每個 `T(c)` 自己連通，而且靠那條回邊接回上面那部分。所以 `G − v` 在這個分量裡仍連通，`v` 不是割點。∎

**定理 B（根）**：根 `r` 是割點 ⇔ `r` 在 DFS 樹上有至少兩個子節點。

**證明**：（⇐）設 `c1`、`c2` 是兩個子節點。`T(c1)` 和 `T(c2)` 的點互不為祖先後代，由引理 1，它們之間沒有任何邊；它們之間的連接只能經過 `r`。刪掉 `r` 就斷開。（⇒）只有一個子節點 `c` 時，`G − r` 就是 `T(c)` 的點集，`T(c)` 本身連通；沒有子節點時 `r` 是孤立點，刪掉不增加分量數。∎

**定理 C（橋）**：非樹邊永遠不是橋。樹邊 `pc`（`p` 是 `c` 的父節點）是橋 ⇔ `low(c) > disc(p)`。

**證明**：非樹邊 `xw` 和樹上 `x` 到 `w` 的路徑形成圈，刪掉它不影響連通。對樹邊 `pc`：除了 `pc` 本身，離開 `T(c)` 的邊都是回邊，通到 `c` 的真祖先，也就是 `p` 或更上面，它們的 disc 值都 `≤ disc(p)`。所以「存在這樣的回邊」⇔ `low(c) ≤ disc(p)`。沒有這種回邊時刪掉 `pc` 就把 `T(c)` 切開；有的話 `T(c)` 經回邊仍接得回去。∎

注意定理 A 用 `≥`、定理 C 用 `>`：子樹的後門剛好通到 `p` 本身時，`p` 是割點（刪 `p` 連後門一起刪了），但 `pc` 不是橋（刪邊時後門還在）。

### 雙連通分量：邊堆疊

把邊在 DFS 中被走到的順序推進一個**邊堆疊**。當子節點 `c` 處理完、發現 `low(c) ≥ disc(p)` 時，`p` 把 `T(c)` 隔開了：從堆疊頂端一路彈到樹邊 `pc` 為止，彈出的邊正好是一個 block。

為什麼正確，這裡給直覺而不是完整證明：「兩條邊在同一個 block」⇔「它們相同或落在同一個圈上」，這是一個等價關係（傳遞性的證明需要一點功夫，West Ch 4 有完整版本）。`low(c) ≥ disc(p)` 時，`T(c)` 往外的所有圈都必須經過 `p`；`T(c)` 內部還沒被彈走的邊（更深的 block 已經先彈走了）加上 `pc`，彼此都能經過 `p` 形成圈，所以是一個 block。下面用 NetworkX 對拍代替形式證明。

## 先看失敗：遞迴版撞上 Python 的遞迴上限

```python
def bridges_recursive(adj):
    disc, low, out, timer = {}, {}, [], [0]

    def dfs(v, parent):
        disc[v] = low[v] = timer[0]
        timer[0] += 1
        for w in adj[v]:
            if w not in disc:
                dfs(w, v)
                low[v] = min(low[v], low[w])
                if low[w] > disc[v]:
                    out.append((v, w))
            elif w != parent:
                low[v] = min(low[v], disc[w])

    for s in adj:
        if s not in disc:
            dfs(s, None)
    return out


small = {0: {1}, 1: {0, 2}, 2: {1}}
print(sorted(bridges_recursive(small)))

n = 5000
path = {i: {j for j in (i - 1, i + 1) if 0 <= j < n} for i in range(n)}
try:
    bridges_recursive(path)
except RecursionError as e:
    print("RecursionError:", e)
```

```text
[(0, 1), (1, 2)]
RecursionError: maximum recursion depth exceeded
```

教科書版本在小圖上完全正確，但一條 5000 個點的路徑就讓它崩潰：CPython 預設遞迴上限是 1000 層（`sys.getrecursionlimit()`）。真實的 CFG、call graph、網路拓撲動輒上萬個點，DFS 深度可以和點數一樣大。調高 `sys.setrecursionlimit` 不是解法——超過 C stack 的容量時直譯器會直接 segfault，而那個界線依平台與執行緒堆疊大小而定。

## 迭代版 Tarjan：割點、橋、block 一次到位

迭代化的訣竅是讓堆疊的每一格保存「這個點的鄰接迭代器」，模擬遞迴函式的區域狀態；「遞迴返回」的那段程式（更新父節點的 `low`、判斷割點與橋）搬到彈出堆疊的時候做。

```python
import random
import networkx as nx


def tarjan_biconnected(nodes, edges):
    """迭代版 Tarjan low-link。edges 是 (u, v) 列表（允許平行邊，不允許自環）。
    回傳 (割點集合, 橋的邊編號集合, 雙連通分量列表（每個是邊編號集合）)。"""
    inc = {v: [] for v in nodes}
    for i, (u, v) in enumerate(edges):
        inc[u].append((v, i))
        inc[v].append((u, i))
    disc, low = {}, {}
    cut, bridges, blocks = set(), set(), []
    timer = 0
    for root in nodes:
        if root in disc:
            continue
        disc[root] = low[root] = timer
        timer += 1
        root_children = 0
        estack = []                                  # 邊堆疊：還沒歸到任何 block 的邊
        stack = [(root, -1, iter(inc[root]))]        # (點, 走進來的邊編號, 鄰接迭代器)
        while stack:
            v, pe, it = stack[-1]
            advanced = False
            for w, eid in it:
                if eid == pe:
                    continue                         # 只跳過「同一條」父邊，平行邊照算
                if w not in disc:                    # 樹邊：往下走
                    disc[w] = low[w] = timer
                    timer += 1
                    estack.append(eid)
                    stack.append((w, eid, iter(inc[w])))
                    advanced = True
                    break
                if disc[w] < disc[v]:                # 往祖先的回邊（每條只推一次）
                    estack.append(eid)
                    low[v] = min(low[v], disc[w])
            if advanced:
                continue
            stack.pop()                              # v 的子樹處理完，回報給父親 p
            if not stack:
                break
            p = stack[-1][0]
            low[p] = min(low[p], low[v])
            if low[v] > disc[p]:
                bridges.add(pe)
            if low[v] >= disc[p]:                    # p 把 v 的子樹「隔開」了
                if p == root:
                    root_children += 1
                else:
                    cut.add(p)
                block = set()
                while True:
                    e = estack.pop()
                    block.add(e)
                    if e == pe:
                        break
                blocks.append(block)
        if root_children >= 2:
            cut.add(root)
    return cut, bridges, blocks


# 範例：兩個三角形用一條橋 2–3 連起來，再在 5 掛一個葉子 6
edges = [(0, 1), (1, 2), (2, 0), (2, 3), (3, 4), (4, 5), (5, 3), (5, 6)]
cut, br, blocks = tarjan_biconnected(range(7), edges)
print("割點:", sorted(cut))
print("橋:", sorted(edges[i] for i in br))
print("blocks:", sorted(sorted({x for i in b for x in edges[i]}) for b in blocks))

# 對拍：隨機簡單圖（含不連通、樹、稠密圖）
rng = random.Random(11)
for t in range(1500):
    n = rng.randint(1, 14)
    G = nx.gnp_random_graph(n, rng.choice([0.1, 0.2, 0.35, 0.6]), seed=rng.randint(0, 10**9))
    E = list(G.edges())
    cut, br, blocks = tarjan_biconnected(list(G), E)
    assert cut == set(nx.articulation_points(G))
    assert {frozenset(E[i]) for i in br} == {frozenset(e) for e in nx.bridges(G)}
    mine = sorted(sorted({x for i in b for x in E[i]}) for b in blocks)
    assert mine == sorted(sorted(c) for c in nx.biconnected_components(G))
print("1500 張隨機圖：割點、橋、雙連通分量全部與 NetworkX 一致")

# 平行邊：兩條 0–1 的邊不是橋
edges = [(0, 1), (0, 1), (1, 2)]
_, br, _ = tarjan_biconnected(range(3), edges)
print("多重圖的橋:", sorted(edges[i] for i in br),
      "  NetworkX:", sorted(nx.bridges(nx.MultiGraph(edges))))

# 深度 20 萬的路徑：迭代版不怕遞迴上限
n = 200_000
cut, br, blocks = tarjan_biconnected(range(n), [(i, i + 1) for i in range(n - 1)])
print("長路徑:", len(cut), "個割點,", len(br), "條橋,", len(blocks), "個 block")
```

```text
割點: [2, 3, 5]
橋: [(2, 3), (5, 6)]
blocks: [[0, 1, 2], [2, 3], [3, 4, 5], [5, 6]]
1500 張隨機圖：割點、橋、雙連通分量全部與 NetworkX 一致
多重圖的橋: [(1, 2)]   NetworkX: [(1, 2)]
長路徑: 199998 個割點, 199999 條橋, 199999 個 block
```

逐行對照證明：

- `eid == pe` 只跳過「走進來的那一條邊」，而不是「通往父節點的所有邊」。平行邊因此被當成回邊，`low(c) ≤ disc(p)`，正確地讓 `pc` 不是橋（定理 C 證明裡的「除了 `pc` 本身」）。
- `disc[w] < disc[v]` 的條件讓每條回邊只在「後代那一端」被處理一次；從祖先那一端看到它時，`w` 是已處理完的後代，不重複推進邊堆疊。這正是引理 1 的保證：已發現的鄰居不是祖先就是後代。
- 彈出 `v` 時才用 `low[v]` 更新父節點，對應遞迴版的 `low[v] = min(low[v], low[w])` 寫在遞迴呼叫**之後**。
- 根的特判：`low[v] >= disc[root]` 對根的每個子節點都成立（沒有比根更早的點），所以根要另外數子節點個數（定理 B）。

## 故意弄壞：平行邊與「跳過父節點」

很多教科書與刷題模板寫 `elif w != parent`——用**點**判斷父邊。在簡單圖上沒問題，在多重圖上就錯：

```python
def bridges_skip_parent_vertex(adj):
    """常見寫法：遇到「父節點」就跳過。adj 用 list 才能表示平行邊。"""
    disc, low, out = {}, {}, []

    def dfs(v, parent):
        disc[v] = low[v] = len(disc)
        for w in adj[v]:
            if w not in disc:
                dfs(w, v)
                low[v] = min(low[v], low[w])
                if low[w] > disc[v]:
                    out.append((v, w))
            elif w != parent:                     # 錯在這裡：把通往父節點的平行邊也跳過了
                low[v] = min(low[v], disc[w])

    dfs(0, None)
    return out


adj = {0: [1, 1], 1: [0, 0, 2], 2: [1]}          # 0–1 有兩條平行邊
print(bridges_skip_parent_vertex(adj))
```

```text
[(1, 2), (0, 1)]
```

`0–1` 有兩條線路，斷一條另一條還在，不是橋——但這個寫法把它回報成橋。在網路拓撲分析裡，「兩台交換器之間有兩條備援線」是非常常見的設計，這個 bug 會產生假的 SPOF 警報。修正方法就是上面迭代版的作法：用邊編號判斷。

## Whitney 不等式：κ ≤ λ ≤ δ

割點與橋是「連通度 1」的情況。一般的連通度滿足：

**定理（Whitney 1932）**：對任何簡單圖 `G`，`κ(G) ≤ λ(G) ≤ δ(G)`。

**證明**：

`λ ≤ δ`：把最小度數點的所有邊刪掉，它就被孤立了（`n ≥ 2` 時）。

`κ ≤ λ`：若 `G` 是完全圖，`κ = λ = n − 1`（慣例：`κ(K_n) = n − 1`）。否則取一個大小為 `λ` 的最小邊割 `F`，它把點分成 `S` 和 `V∖S`。

- 若 `S` 的每個點都和 `V∖S` 的每個點相鄰，則 `λ = |F| = |S|·|V∖S| ≥ n − 1 ≥ κ`（`a + b = n`、`a, b ≥ 1` 時 `ab ≥ n − 1`）。
- 否則取不相鄰的 `x ∈ S`、`y ∈ V∖S`。對 `F` 的每條邊選一個端點刪掉：若邊碰到 `x`，刪它在 `V∖S` 的那一端（不會是 `y`，因為 `x ≁ y`）；否則刪它在 `S` 的那一端（不會是 `x`）。至多刪了 `λ` 個點，`x`、`y` 都沒被刪。任何 `x` 到 `y` 的路徑都要經過 `F` 的某條邊，而那條邊有一端被刪了，所以 `x`、`y` 不再連通，`κ ≤ λ`。∎

```python
import networkx as nx

tight = {"κ<λ": 0, "λ<δ": 0}
total = 0
for G in nx.graph_atlas_g():
    if G.number_of_nodes() < 2 or not nx.is_connected(G):
        continue
    k = nx.node_connectivity(G)
    lam = nx.edge_connectivity(G)
    d = min(dict(G.degree()).values())
    assert k <= lam <= d, (sorted(G.edges()), k, lam, d)
    total += 1
    tight["κ<λ"] += k < lam
    tight["λ<δ"] += lam < d
print(f"{total} 張連通圖（2–7 點）都滿足 κ ≤ λ ≤ δ；嚴格不等的個數 {tight}")

# 領結圖（bowtie）：兩個三角形共用一點 → κ=1、λ=2、δ=2
B = nx.Graph([(0, 1), (1, 2), (2, 0), (2, 3), (3, 4), (4, 2)])
print("bowtie:", nx.node_connectivity(B), nx.edge_connectivity(B), min(d for _, d in B.degree()))
```

```text
995 張連通圖（2–7 點）都滿足 κ ≤ λ ≤ δ；嚴格不等的個數 {'κ<λ': 58, 'λ<δ': 6}
bowtie: 1 2 2
```

領結圖是 `κ < λ` 的最小例子：中間那個點是割點，但沒有橋。`λ < δ` 的例子要大一點，例如兩個 `K4` 用一條邊連起來：`δ = 3`、`λ = 1`。`nx.node_connectivity` 和 `nx.edge_connectivity` 內部用的是最大流——Menger 定理（Ch 12）保證「最少要刪幾個點」等於「最多有幾條點不相交路徑」，而後者可以用 flow 算（Ch 20）。

## 對比與取捨

| 方法 | 找什麼 | 時間 | 優點 | 缺點 |
| --- | --- | --- | --- | --- |
| 逐點／逐邊刪除 + BFS | 割點、橋 | `O(n(n + m))` / `O(m(n + m))` | 不會寫錯 | 慢；適合當對拍標準答案 |
| Tarjan low-link（本章） | 割點、橋、block | `O(n + m)` | 一次 DFS 全找到 | 細節多（父邊、根、平行邊） |
| Chain decomposition（Schmidt 2013） | 2-連通、2-邊連通測試，也能找割點與橋 | `O(n + m)` | 概念簡單，NetworkX 的 `bridges` 用它 | 較少教科書介紹 |
| 最大流（Ch 20） | 一般 `κ`、`λ` | 多項式但大得多 | 適用任意 `k` | 對 `k = 1, 2` 大材小用 |

## 踩雷集錦

1. **用點而不是邊判斷父邊**：如上所示，多重圖上把平行邊誤判成橋。即使你的圖是簡單圖，用邊編號也不會更慢，養成習慣。
2. **割點用 `>`、橋用 `≥`（寫反）**：記法是「刪點時，連通到 `p` 本身的後門也被刪掉了，所以剛好通到 `p` 也算被隔開 → `≥`」。定理 A 和 C 的證明差別就在這裡。
3. **忘記根的特判**：根沒有祖先，`low(c) ≥ disc(root)` 對所有子節點恆成立；不特判的話，每個至少有一個子節點的根都會被當成割點。
4. **以為「沒有割點」⇔「沒有橋」**：領結圖有割點沒有橋。反方向，`n ≥ 3` 的連通圖若有橋，橋的某一端就是割點（另一端若是葉子則不是）；但兩點一邊的 `K2` 有橋卻沒有割點。
5. **遞迴版在大圖上崩潰**：不要靠 `sys.setrecursionlimit` 硬撐。迭代版的程式碼長一點，但它是唯一在任意輸入下都安全的寫法。

## 進階：再往深一層

- **Block-cut tree**：把每個 block 和每個割點各當成一個節點，block 包含割點就連邊，得到的圖是一棵樹（若 `G` 連通）。很多問題（例如「兩點間所有路徑都必經的點」）在 block-cut tree 上變成樹上路徑問題。
- **3-連通與 SPQR tree**：把「割點」推廣到「分離對（separation pair）」，Hopcroft–Tarjan（1973）給出線性時間的三連通分量演算法；SPQR tree 是平面性測試（Ch 18）的重要資料結構。
- **動態連通度**：邊會被加入和刪除時，維護「是否連通」「是不是橋」有 polylog 攤銷時間的資料結構（Holm–de Lichtenberg–Thorup），是分散式系統監控拓撲時的理論基礎。
- **資安應用**：BloodHound 類工具把 AD 物件與權限建成有向圖；在「無向化」的近似下，block-cut tree 能快速找出所有路徑都必經的群組或主機。有向版本的「必經點」正是 Ch 14 的 dominator。

## 動手練習

1. 寫一個暴力版 `cut_vertices_brute`（每個點刪掉後跑 BFS 數分量），在 1000 張隨機圖上和迭代版對拍；再用 `time.perf_counter` 比較兩者在 `n = 2000` 稀疏圖上的時間。
2. 建立 block-cut tree：用本章回傳的 `blocks` 與 `cut`，建出它並用 `nx.is_tree` 驗證；思考 `G` 不連通時會得到什麼。
3. 只靠 `tarjan_biconnected` 的輸出，求「刪掉一條邊後最大連通分量最小」的那條橋。（提示：需要知道每條橋兩側的大小，在 DFS 時順便算子樹大小。）
4. 把 `disc[w] < disc[v]` 這個條件拿掉，看看 block 會出什麼錯，並用引理 1 解釋原因。

## 本章重點整理

- 無向 DFS 沒有橫向邊：非樹邊都連接祖先與後代（引理 1）。所有判準都建立在它上面。
- `low(v)` = 子樹透過一條回邊最高能爬到的發現時間。非根割點 ⇔ 有子節點 `low(c) ≥ disc(v)`；根是割點 ⇔ 至少兩個子節點；樹邊 `pc` 是橋 ⇔ `low(c) > disc(p)`。
- 邊堆疊在 `low(c) ≥ disc(p)` 時彈出一個 block。整個演算法 `O(n + m)`。
- Python 實作要寫迭代版，並用**邊編號**而非父節點判斷父邊，才能處理深圖與平行邊。
- Whitney：`κ ≤ λ ≤ δ`；一般的 `κ`、`λ` 靠 Menger + flow 計算。

## 自我檢核

- [ ] 不看筆記，解釋為什麼無向 DFS 沒有橫向邊，以及有向 DFS 為什麼會有。
- [ ] 能用一句話說出割點判準用 `≥`、橋判準用 `>` 的原因，並畫出一個「`p` 是割點但 `pc` 不是橋」的小圖。
- [ ] 如果同事的 SPOF 偵測腳本把雙線備援的交換器連線標成單點故障，你知道第一個該檢查哪一行。
- [ ] 能在白板上證明 `κ ≤ λ`，並說明為什麼要另外處理「`S` 和 `V∖S` 完全相連」的情況。
- [ ] 說得出遞迴版 DFS 在 Python 裡為什麼不能靠調高遞迴上限解決。

## 延伸閱讀

### 論文

- **[Depth-First Search and Linear Graph Algorithms](https://doi.org/10.1137/0201010)** — Robert Tarjan，SIAM Journal on Computing 1(2):146–160（1972）
  - **核心貢獻**：把 DFS 樹的結構性質（樹邊、回邊、橫向邊的分類）系統化，給出線性時間的雙連通分量與強連通分量演算法；`LOWPT` 就是本章的 `low`。
  - **讀哪裡**：DFS 邊分類的部分與雙連通分量演算法；SCC 的部分留給 Ch 13。
  - **前提**：本章引理 1 與定理 A。
- **[Algorithm 447: Efficient Algorithms for Graph Manipulation](https://doi.org/10.1145/362248.362272)** — John Hopcroft、Robert Tarjan，Communications of the ACM 16(6):372–378（1973）
  - **核心貢獻**：雙連通分量演算法的 CACM「演算法」版本，附實作描述，是後來教科書版本的直接來源。
  - **讀哪裡**：演算法描述與邊堆疊的處理。
  - **前提**：本章的迭代實作。
- **[A simple test on 2-vertex- and 2-edge-connectivity](https://doi.org/10.1016/j.ipl.2013.01.016)** — Jens M. Schmidt，Information Processing Letters 113(7):241–244（2013）
  - **核心貢獻**：用「chain decomposition」——DFS 後沿每條回邊往上走到已走過的點——不用 low 值就能判斷 2-連通與 2-邊連通，並找出割點與橋。
  - **讀哪裡**：全文四頁；對照 NetworkX `bridges` 的原始碼讀。
  - **前提**：本章引理 1。

### 書籍

- **《Introduction to Graph Theory》** — Douglas B. West（第 2 版，2001）
  - **讀哪幾章**：Ch 4「Connectivity and Paths」§4.1：割點、橋、block、block-cut tree、Whitney 不等式，以及本章略過的「同 block 是等價關係」的證明。
  - **前提**：本章。
- **《Introduction to Algorithms》** — Cormen、Leiserson、Rivest、Stein（第 4 版，2022）
  - **讀哪幾章**：Ch 20「Elementary Graph Algorithms」的 DFS 邊分類與括號定理；章末習題正是割點、橋、雙連通分量的 low 值推導。
  - **前提**：寫過 DFS。

### 官方文件

- **[NetworkX `bridges`](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.bridges.bridges.html)**
  - **讀哪裡**：說明它基於 chain decomposition，以及多重圖的處理方式：先轉成簡單圖找橋，再排除有平行邊的那些。這和本章「用邊編號」是兩種解法。
  - **注意**：`articulation_points` 和 `biconnected_components` 在另一個模組，用的是 Tarjan 式的 DFS。

割點與 Whitney 不等式說的是「最少刪幾個點會斷」。下一章證明它的另一面：最少要刪的點數，恰好等於兩點之間最多能找到的互不相交路徑數。

→ [Ch 12 Menger 定理](./12-menger-theorem.md)
